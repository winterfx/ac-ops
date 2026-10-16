module github.com/winterfx/ac-ops/cli

go 1.25.0

// chaitin-cli is pinned to a commit on its main branch. Its vYYMM.x.y tags are
// invisible to Go (a major version above 1 needs a /vN module path), so a
// pseudo-version is the only way to pin it, released or not. To bump:
//   go get github.com/chaitin/chaitin-cli@<commit>
require (
	github.com/chaitin/chaitin-cli v0.0.0-20261015100223-44e257dc3cbc
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
