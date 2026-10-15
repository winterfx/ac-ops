module github.com/winterfx/ac-nightly-report/cli

go 1.25.0

require (
	github.com/chaitin/chaitin-cli v0.0.0
	github.com/spf13/cobra v1.9.1
)

require (
	connectrpc.com/connect v1.19.2 // indirect
	github.com/chaitin/agent-compose/proto v0.1.12 // indirect
	github.com/inconshreveable/mousetrap v1.1.0 // indirect
	github.com/joho/godotenv v1.5.1 // indirect
	github.com/spf13/pflag v1.0.10 // indirect
	golang.org/x/sys v0.41.0 // indirect
	golang.org/x/term v0.40.0 // indirect
	google.golang.org/protobuf v1.36.12 // indirect
	gopkg.in/yaml.v3 v3.0.1 // indirect
)

// chaitin/chaitin-cli#88 (run diagnostics, fast ps) is not released yet; build
// from its commit on the fork. Switch to an upstream version once it ships.
replace github.com/chaitin/chaitin-cli => github.com/winterfx/chaitin-cli v0.0.0-20261015082531-c4aaf38be292
