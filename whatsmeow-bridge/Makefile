.PHONY: deps build build-windows build-linux

# Fetch all dependencies and tidy the module graph. Run once before building.
deps:
	go get go.mau.fi/whatsmeow@latest modernc.org/sqlite@latest github.com/mdp/qrterminal/v3@latest
	go mod tidy

# Build for the current platform.
build:
	go build -o whatsmeow-bridge .

# Cross-compile for Windows x64 (run from Linux/macOS).
build-windows:
	GOOS=windows GOARCH=amd64 go build -o whatsmeow-bridge.exe .

# Cross-compile for Linux x64.
build-linux:
	GOOS=linux GOARCH=amd64 go build -o whatsmeow-bridge .
