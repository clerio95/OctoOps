// Whatsmeow bridge sidecar for OctoOps.
//
// Exposes a local HTTP API that OctoOps drives:
//   GET  /health              → {"ok":true,"logged_in":<bool>}
//   POST /send                ← {"chat_id":"<jid>","text":"<msg>"}
//   POST /register-callback   ← {"url":"http://127.0.0.1:<port>/incoming"}
//   POST /shutdown
//
// Inbound WhatsApp messages are forwarded via POST to the registered callback
// URL with {"sender":"<jid>","text":"<msg>"}.
//
// Build:
//   go get go.mau.fi/whatsmeow@latest modernc.org/sqlite@latest github.com/mdp/qrterminal/v3@latest
//   go mod tidy
//   go build -o whatsmeow-bridge.exe .   # Windows
//   go build -o whatsmeow-bridge .        # Linux/macOS
//
// Flags:
//   --port  HTTP listen port (default 3000, override with BRIDGE_PORT env var)
//   --db    SQLite session DB path (default whatsmeow.db in working dir)
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/mdp/qrterminal/v3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
	_ "modernc.org/sqlite"
)

var (
	waClient     *whatsmeow.Client
	callbackURL  string
	cbMu         sync.RWMutex
	shutdownCh   = make(chan struct{})
	shutdownOnce sync.Once
	httpClient   = &http.Client{Timeout: 5 * time.Second}
)

func main() {
	port := flag.Int("port", envInt("BRIDGE_PORT", 3000), "HTTP listen port")
	dbPath := flag.String("db", "whatsmeow.db", "SQLite session database path")
	flag.Parse()

	dbLog := waLog.Stdout("Database", "ERROR", true)
	container, err := sqlstore.New("sqlite", fmt.Sprintf("file:%s?_foreign_keys=on", *dbPath), dbLog)
	if err != nil {
		log.Fatalf("open db: %v", err)
	}

	deviceStore, err := container.GetFirstDevice()
	if err != nil {
		log.Fatalf("get device: %v", err)
	}

	clientLog := waLog.Stdout("Client", "INFO", true)
	waClient = whatsmeow.NewClient(deviceStore, clientLog)
	waClient.AddEventHandler(onEvent)

	// Start the HTTP server before connecting to WhatsApp so OctoOps health
	// polls succeed immediately while QR scanning is in progress.
	addr := fmt.Sprintf("127.0.0.1:%d", *port)
	mux := http.NewServeMux()
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/send", handleSend)
	mux.HandleFunc("/register-callback", handleRegisterCallback)
	mux.HandleFunc("/shutdown", handleShutdown)
	srv := &http.Server{Addr: addr, Handler: mux}
	go func() {
		log.Printf("bridge listening on %s", addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Printf("http: %v", err)
		}
	}()

	go connectWA()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	select {
	case <-sig:
	case <-shutdownCh:
	}

	log.Println("shutting down")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = srv.Shutdown(ctx)
	waClient.Disconnect()
}

// connectWA connects to WhatsApp. On first run (no stored session) it prints a
// QR code to stdout and waits for the user to scan it.
func connectWA() {
	if waClient.Store.ID == nil {
		qrChan, _ := waClient.GetQRChannel(context.Background())
		if err := waClient.Connect(); err != nil {
			log.Printf("wa connect: %v", err)
			return
		}
		fmt.Println("\nScan this QR code in WhatsApp → Settings → Linked Devices → Link a Device:\n")
		for evt := range qrChan {
			switch evt.Event {
			case "code":
				qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)
			case "success":
				fmt.Println("Paired successfully.")
			default:
				log.Printf("qr event: %s", evt.Event)
			}
		}
	} else {
		if err := waClient.Connect(); err != nil {
			log.Printf("wa connect: %v", err)
		}
	}
}

// onEvent handles incoming WhatsApp events. Only private text messages are
// forwarded to the registered callback URL.
func onEvent(evt interface{}) {
	msg, ok := evt.(*events.Message)
	if !ok || msg.Info.IsFromMe || msg.Info.IsGroup || msg.Message == nil {
		return
	}

	var text string
	switch {
	case msg.Message.GetConversation() != "":
		text = msg.Message.GetConversation()
	case msg.Message.GetExtendedTextMessage() != nil:
		text = msg.Message.GetExtendedTextMessage().GetText()
	}
	if text == "" {
		return
	}

	cbMu.RLock()
	url := callbackURL
	cbMu.RUnlock()
	if url == "" {
		return
	}

	sender := msg.Info.Sender.String()
	payload, _ := json.Marshal(map[string]string{"sender": sender, "text": text})
	go postCallback(url, payload)
}

func postCallback(url string, payload []byte) {
	resp, err := httpClient.Post(url, "application/json", bytes.NewReader(payload))
	if err != nil {
		log.Printf("callback post: %v", err)
		return
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
}

// --- HTTP handlers -----------------------------------------------------------

func handleHealth(w http.ResponseWriter, _ *http.Request) {
	loggedIn := waClient.IsConnected() && waClient.Store.ID != nil
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"ok":        true,
		"logged_in": loggedIn,
	})
}

type sendReq struct {
	ChatID string `json:"chat_id"`
	Text   string `json:"text"`
}

func handleSend(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errResp("method not allowed"))
		return
	}
	var req sendReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.ChatID == "" || req.Text == "" {
		writeJSON(w, http.StatusBadRequest, errResp("invalid request"))
		return
	}
	jid, err := parseJID(req.ChatID)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, errResp("invalid chat_id"))
		return
	}
	msg := &waE2E.Message{Conversation: proto.String(req.Text)}
	if _, err := waClient.SendMessage(context.Background(), jid, msg); err != nil {
		log.Printf("send: %v", err)
		writeJSON(w, http.StatusInternalServerError, errResp("send failed"))
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true})
}

type cbReq struct {
	URL string `json:"url"`
}

func handleRegisterCallback(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errResp("method not allowed"))
		return
	}
	var req cbReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.URL == "" {
		writeJSON(w, http.StatusBadRequest, errResp("invalid request"))
		return
	}
	cbMu.Lock()
	callbackURL = req.URL
	cbMu.Unlock()
	log.Printf("callback registered: %s", req.URL)
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true})
}

func handleShutdown(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errResp("method not allowed"))
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true})
	shutdownOnce.Do(func() { close(shutdownCh) })
}

// --- helpers -----------------------------------------------------------------

// parseJID accepts a full JID ("5511...@s.whatsapp.net") or a bare phone
// number ("5511..."), appending the individual suffix when needed.
func parseJID(s string) (types.JID, error) {
	if jid, err := types.ParseJID(s); err == nil {
		return jid, nil
	}
	return types.ParseJID(s + "@s.whatsapp.net")
}

func writeJSON(w http.ResponseWriter, code int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func errResp(msg string) map[string]interface{} {
	return map[string]interface{}{"ok": false, "error": msg}
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		var n int
		if _, err := fmt.Sscanf(v, "%d", &n); err == nil && n > 0 {
			return n
		}
	}
	return def
}
