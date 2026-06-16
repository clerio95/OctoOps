// Whatsmeow bridge sidecar for OctoOps.
//
// Exposes a local HTTP API that OctoOps drives:
//   GET  /health              → {"ok":true,"logged_in":<bool>,"outdated":<bool>}
//   GET  /resolve-lid?pn=<n>  → {"ok":true,"pn":"<pn-jid>","lid":"<lid-jid>"}
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
//
// Auth:
//   BRIDGE_TOKEN env var — when set, every endpoint requires
//   "Authorization: Bearer <token>" and the outbound callback presents it.
//   OctoOps mints this per process and passes it when spawning the bridge.
//   Empty (e.g. interactive pairing) = unauthenticated, loopback only.
//
// Diagnostics / overrides (env vars):
//   WA_VERSION          — force the WhatsApp-Web client version (e.g.
//                         "2.3000.1041485407"). Escape hatch for a 405/"couldn't
//                         link device" when whatsmeow's embedded version lags the
//                         server requirement. Normally leave unset and rebuild
//                         against the latest whatsmeow instead.
//   BRIDGE_DEBUG_EVENTS — when set (any value), raises the whatsmeow Client log to
//                         DEBUG and logs every unrecognised event type. Use for a
//                         single pairing-failure diagnosis; leave unset for 24/7.
package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/mdp/qrterminal/v3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
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
	// Shared secret with OctoOps (passed in via BRIDGE_TOKEN). When set, every
	// endpoint requires "Authorization: Bearer <token>" and the outbound callback
	// presents it. Empty = unauthenticated (legacy / interactive pairing only).
	bridgeToken string
	// Set when WhatsApp rejects this client as outdated (error 405). Surfaced in
	// /health so OctoOps can auto-rebuild the bridge instead of failing silently.
	clientOutdated atomic.Bool
	// When true (BRIDGE_DEBUG_EVENTS set) the whatsmeow Client logger runs at DEBUG
	// and every unrecognised event type is logged — for diagnosing pairing failures.
	debugEvents bool
)

// authOK reports whether a request may proceed: true when no token is configured,
// otherwise a constant-time match of the bearer Authorization header.
func authOK(r *http.Request) bool {
	if bridgeToken == "" {
		return true
	}
	expected := "Bearer " + bridgeToken
	got := r.Header.Get("Authorization")
	return subtle.ConstantTimeCompare([]byte(got), []byte(expected)) == 1
}

func main() {
	port := flag.Int("port", envInt("BRIDGE_PORT", 3000), "HTTP listen port")
	dbPath := flag.String("db", "whatsmeow.db", "SQLite session database path")
	flag.Parse()

	bridgeToken = os.Getenv("BRIDGE_TOKEN")
	if bridgeToken == "" {
		log.Println("warning: BRIDGE_TOKEN not set — HTTP API is unauthenticated")
	}
	debugEvents = os.Getenv("BRIDGE_DEBUG_EVENTS") != ""

	dbLog := waLog.Stdout("Database", "ERROR", true)
	// whatsmeow issues many concurrent writes during history sync (signal-store
	// migration, prekeys, identities). Without busy_timeout a second writer hits
	// SQLITE_BUSY immediately and the write is lost — which corrupts session/identity
	// state and makes inbound messages fail to decrypt. busy_timeout makes writers
	// wait; WAL lets reads proceed alongside a writer. Both are per-connection and
	// applied to every pooled connection via the DSN.
	dsn := fmt.Sprintf("file:%s?_pragma=foreign_keys(1)&_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)", *dbPath)
	container, err := sqlstore.New(context.Background(), "sqlite", dsn, dbLog)
	if err != nil {
		log.Fatalf("open db: %v", err)
	}
	// The session DB holds WhatsApp encryption keys — restrict to the owner.
	// Best-effort: POSIX honours the mode; on Windows (NTFS ACLs) it's a no-op
	// and the install dir's ACL is the real guard (see setup.ps1 icacls).
	for _, p := range []string{*dbPath, *dbPath + "-wal", *dbPath + "-shm"} {
		if _, statErr := os.Stat(p); statErr == nil {
			if chErr := os.Chmod(p, 0o600); chErr != nil {
				log.Printf("warning: chmod %s: %v", p, chErr)
			}
		}
	}

	deviceStore, err := container.GetFirstDevice(context.Background())
	if err != nil {
		log.Fatalf("get device: %v", err)
	}

	clientLevel := "INFO"
	if debugEvents {
		// Full whatsmeow handshake/stream detail for a one-off pairing diagnosis.
		clientLevel = "DEBUG"
	}
	clientLog := waLog.Stdout("Client", clientLevel, true)
	waClient = whatsmeow.NewClient(deviceStore, clientLog)
	waClient.AddEventHandler(onEvent)
	applyWAVersion()

	// Start the HTTP server before connecting to WhatsApp so OctoOps health
	// polls succeed immediately while QR scanning is in progress.
	addr := fmt.Sprintf("127.0.0.1:%d", *port)
	mux := http.NewServeMux()
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/resolve-lid", handleResolveLID)
	mux.HandleFunc("/send", handleSend)
	mux.HandleFunc("/groups", handleGroups)
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
		fmt.Println("\nScan this QR code in WhatsApp → Settings → Linked Devices → Link a Device:")
		fmt.Println("(QR codes refresh every ~20 s — always scan the latest one)\n")
		for evt := range qrChan {
			switch evt.Event {
			case "code":
				fmt.Println("--- new QR code ---")
				qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)
			case "success":
				fmt.Println("Paired successfully.")
			case "timeout":
				log.Println("[BRIDGE] QR pairing timed out — no scan within the window. Restart the bridge to get a fresh QR.")
			case "err-client-outdated":
				// THE pair-time outdated signal — never reaches ConnectFailure on a
				// fresh login, so this is what the phone's "couldn't link device,
				// check your connection" actually means.
				clientOutdated.Store(true)
				log.Println("==================================================================")
				log.Println("[BRIDGE] QR pairing rejected: CLIENT OUTDATED (405).")
				log.Println("[BRIDGE] WhatsApp deprecated this whatsmeow version at pair time.")
				log.Println("[BRIDGE] Fix: rebuild against the latest whatsmeow, or set")
				log.Println("[BRIDGE] WA_VERSION to a current WhatsApp-Web version and retry.")
				log.Println("==================================================================")
			default:
				// "error", "err-unexpected-state", etc. — a problem the phone may
				// show as "couldn't link device". Surface it instead of swallowing.
				log.Printf("[BRIDGE] QR event %q (error=%v) — pairing did not complete.", evt.Event, evt.Error)
			}
		}
	} else {
		if err := waClient.Connect(); err != nil {
			log.Printf("wa connect: %v", err)
		}
	}
}

// onEvent handles incoming WhatsApp events. Private text messages are forwarded
// to the registered callback URL; connection failures are logged with an
// actionable hint so an outdated client is obvious instead of a silent outage.
func onEvent(evt interface{}) {
	switch v := evt.(type) {
	case *events.Message:
		handleMessage(v)
	case *events.ConnectFailure:
		onConnectFailure(v)
	case *events.StreamError:
		// A stream:error during the QR handshake is how WhatsApp rejects a *pair*
		// attempt — it never reaches ConnectFailure on a fresh (sessionless) login,
		// so the only sign was previously the phone's "couldn't link device". An
		// outdated client surfaces here as code "405".
		if v.Code == "405" {
			clientOutdated.Store(true)
		}
		log.Printf("[BRIDGE] stream error: code=%q", v.Code)
	case *events.PairSuccess:
		log.Printf("[BRIDGE] pair success: id=%s platform=%q business=%q", v.ID, v.Platform, v.BusinessName)
	case *events.PairError:
		// Pairing was rejected after the QR scan (the phone shows "couldn't link
		// device, check your connection and try again"). Log the real reason.
		log.Printf("[BRIDGE] PAIR ERROR: id=%s error=%v", v.ID, v.Error)
	case *events.ClientOutdated:
		clientOutdated.Store(true)
		log.Println("[BRIDGE] client outdated — WhatsApp deprecated this whatsmeow version; rebuild against the latest whatsmeow (or set WA_VERSION).")
	case *events.QRScannedWithoutMultidevice:
		log.Println("[BRIDGE] QR scanned but multi-device is not enabled on the phone — open WhatsApp → Linked Devices and try again.")
	case *events.TemporaryBan:
		log.Printf("[BRIDGE] TEMPORARY BAN: code=%v expires_in=%s", v.Code, v.Expire)
	case *events.LoggedOut:
		log.Printf("[BRIDGE] logged out: on_connect=%v reason=%s", v.OnConnect, v.Reason)
	case *events.StreamReplaced:
		log.Println("[BRIDGE] stream replaced — another client took over this session.")
	case *events.Connected:
		log.Println("[BRIDGE] connected to WhatsApp.")
	case *events.Disconnected:
		log.Println("[BRIDGE] disconnected from WhatsApp.")
	case *events.CATRefreshError:
		log.Printf("[BRIDGE] CAT refresh error: %v", v.Error)
	default:
		if debugEvents {
			log.Printf("[BRIDGE] event: %T", v)
		}
	}
}

// applyWAVersion logs the WhatsApp-Web protocol version this client presents and
// lets an operator force it via WA_VERSION (e.g. "2.3000.1041485407"). WhatsApp
// rejects clients whose version it has deprecated (error 405 at connect, or a
// silent "couldn't link device" on the phone at pair time); normally the fix is to
// rebuild against the latest whatsmeow, but the override is an escape hatch for
// when whatsmeow's embedded version still lags the server requirement.
func applyWAVersion() {
	if v := os.Getenv("WA_VERSION"); v != "" {
		parsed, err := store.ParseVersion(v)
		if err != nil {
			log.Printf("[BRIDGE] ignoring invalid WA_VERSION %q: %v", v, err)
		} else {
			store.SetWAVersion(parsed)
			log.Printf("[BRIDGE] WA_VERSION override applied: %s", parsed)
		}
	}
	log.Printf("[BRIDGE] WhatsApp-Web client version: %s", store.GetWAVersion())
}

// onConnectFailure logs WhatsApp connection rejections. Reason 405 means WhatsApp
// has deprecated this client's embedded WhatsApp-Web version (the usual cause of
// "scan QR → phone instantly says check your connection"); the only fix is to
// update whatsmeow and rebuild.
func onConnectFailure(v *events.ConnectFailure) {
	if v.Reason == events.ConnectFailureClientOutdated {
		// Surface the outdated state in /health so OctoOps can auto-rebuild.
		clientOutdated.Store(true)
		log.Println("==================================================================")
		log.Println("[BRIDGE] WhatsApp rejected this client as OUTDATED (error 405).")
		log.Println("[BRIDGE] The embedded whatsmeow version is too old for WhatsApp.")
		log.Println("[BRIDGE] Fix: in whatsmeow-bridge/ run:")
		log.Println("[BRIDGE]   go get -u go.mau.fi/whatsmeow@latest && go mod tidy")
		log.Println("[BRIDGE]   go build -o ..\\whatsmeow-bridge.exe .")
		log.Println("[BRIDGE] then delete whatsmeow.db* and re-pair.")
		log.Println("==================================================================")
		return
	}
	log.Printf("[BRIDGE] WhatsApp connect failure: reason=%d message=%q", v.Reason, v.Message)
}

// handleMessage forwards an inbound private text message to the callback URL.
func handleMessage(msg *events.Message) {
	if msg.Info.IsFromMe || msg.Info.IsGroup || msg.Message == nil {
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

	// sender is what we reply to (the address WhatsApp used — may be a @lid).
	// sender_pn is the phone number, so OctoOps can match allowlists that list
	// phone numbers even when WhatsApp delivers the message under an opaque LID.
	sender := msg.Info.Sender.String()
	payload, _ := json.Marshal(map[string]string{
		"sender":    sender,
		"sender_pn": resolvePN(msg.Info),
		"text":      text,
	})
	go postCallback(url, payload)
}

// resolvePN returns the phone-number JID for a message's sender, or "" if it
// can't be determined. WhatsApp may address a sender by an opaque LID instead of
// their phone number; SenderAlt carries the phone number in that case (delivered
// with the message), with a store lookup as a best-effort fallback. Operators
// allowlist phone numbers, so OctoOps needs the PN to match seamlessly.
func resolvePN(info types.MessageInfo) string {
	if info.AddressingMode != types.AddressingModeLID {
		// Sender is already addressed by phone number.
		return info.Sender.String()
	}
	if !info.SenderAlt.IsEmpty() && info.SenderAlt.User != "" {
		return info.SenderAlt.String()
	}
	if waClient != nil {
		if pn, err := waClient.Store.LIDs.GetPNForLID(context.Background(), info.Sender); err == nil && pn.User != "" {
			return pn.String()
		}
	}
	return ""
}

func postCallback(url string, payload []byte) {
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(payload))
	if err != nil {
		log.Printf("callback build: %v", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if bridgeToken != "" {
		req.Header.Set("Authorization", "Bearer "+bridgeToken)
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		log.Printf("callback post: %v", err)
		return
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
}

// --- HTTP handlers -----------------------------------------------------------

func handleGroups(w http.ResponseWriter, r *http.Request) {
	if !authOK(r) {
		writeJSON(w, http.StatusUnauthorized, errResp("unauthorized"))
		return
	}
	if !waClient.IsConnected() || waClient.Store.ID == nil {
		writeJSON(w, http.StatusServiceUnavailable, errResp("not logged in"))
		return
	}
	groups, err := waClient.GetJoinedGroups(context.Background())
	if err != nil {
		log.Printf("get groups: %v", err)
		writeJSON(w, http.StatusInternalServerError, errResp("groups fetch failed"))
		return
	}
	type groupEntry struct {
		JID          string `json:"jid"`
		Name         string `json:"name"`
		Participants int    `json:"participants"`
	}
	result := make([]groupEntry, 0, len(groups))
	for _, g := range groups {
		result = append(result, groupEntry{
			JID:          g.JID.String(),
			Name:         g.Name,
			Participants: len(g.Participants),
		})
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "groups": result})
}

// handleResolveLID maps a phone number to the opaque LID WhatsApp uses to address
// that contact in inbound messages. WhatsApp increasingly delivers messages under
// a LID instead of the phone number, so a phone-number allowlist never matches; by
// resolving the LID once (right after pairing) OctoOps can allow it automatically
// instead of making the operator hand-copy it from a dropped-message log.
//
// IsOnWhatsApp runs a live usync query (works without prior contact) which both
// confirms the number is on WhatsApp and populates the PN↔LID mapping store, so the
// subsequent GetLIDForPN returns the LID. "lid" is "" when WhatsApp doesn't expose
// one for this contact (the caller then keeps using the phone number).
func handleResolveLID(w http.ResponseWriter, r *http.Request) {
	if !authOK(r) {
		writeJSON(w, http.StatusUnauthorized, errResp("unauthorized"))
		return
	}
	if !waClient.IsConnected() || waClient.Store.ID == nil {
		writeJSON(w, http.StatusServiceUnavailable, errResp("not logged in"))
		return
	}
	pn := strings.TrimSpace(r.URL.Query().Get("pn"))
	if pn == "" {
		writeJSON(w, http.StatusBadRequest, errResp("missing pn"))
		return
	}
	// IsOnWhatsApp wants a dialable phone string; strip any JID server and ensure a
	// leading '+'. A non-phone input (e.g. someone passing a raw LID) simply comes
	// back IsIn=false, which the caller treats as "nothing to resolve".
	query := pn
	if at := strings.IndexRune(query, '@'); at >= 0 {
		query = query[:at]
	}
	if !strings.HasPrefix(query, "+") {
		query = "+" + query
	}
	ctx := context.Background()
	resp, err := waClient.IsOnWhatsApp(ctx, []string{query})
	if err != nil || len(resp) == 0 {
		log.Printf("resolve-lid: IsOnWhatsApp %q: %v", query, err)
		writeJSON(w, http.StatusOK, map[string]interface{}{"ok": false, "error": "lookup failed"})
		return
	}
	entry := resp[0]
	if !entry.IsIn {
		writeJSON(w, http.StatusOK, map[string]interface{}{"ok": false, "error": "not on whatsapp"})
		return
	}
	lidStr := ""
	if lid, lerr := waClient.Store.LIDs.GetLIDForPN(ctx, entry.JID); lerr == nil && lid.User != "" {
		lidStr = lid.String()
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"ok":  true,
		"pn":  entry.JID.String(),
		"lid": lidStr,
	})
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	if !authOK(r) {
		writeJSON(w, http.StatusUnauthorized, errResp("unauthorized"))
		return
	}
	loggedIn := waClient.IsConnected() && waClient.Store.ID != nil
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"ok":        true,
		"logged_in": loggedIn,
		"outdated":  clientOutdated.Load(),
	})
}

type sendReq struct {
	ChatID string `json:"chat_id"`
	Text   string `json:"text"`
}

func handleSend(w http.ResponseWriter, r *http.Request) {
	if !authOK(r) {
		writeJSON(w, http.StatusUnauthorized, errResp("unauthorized"))
		return
	}
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
	if !authOK(r) {
		writeJSON(w, http.StatusUnauthorized, errResp("unauthorized"))
		return
	}
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
	if !authOK(r) {
		writeJSON(w, http.StatusUnauthorized, errResp("unauthorized"))
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errResp("method not allowed"))
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true})
	shutdownOnce.Do(func() { close(shutdownCh) })
}

// --- helpers -----------------------------------------------------------------

// parseJID accepts a full JID ("5511...@s.whatsapp.net", a group "...@g.us", a
// "...@lid") or a bare phone number ("5511..."), appending the individual suffix
// when no server is present. A bare number must NOT be handed straight to
// types.ParseJID: with no '@' it parses the digits as the *server* (empty user),
// which whatsmeow then rejects as "unknown server". So append the suffix first.
func parseJID(s string) (types.JID, error) {
	if !strings.ContainsRune(s, '@') {
		s += "@s.whatsapp.net"
	}
	return types.ParseJID(s)
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
