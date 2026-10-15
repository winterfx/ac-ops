// Command agent-compose-ro is a read-only build of chaitin-cli's agent-compose
// command for the ac-ops agents.
//
// Only the agent-compose product is compiled in, which keeps the binary small,
// and dry-run is always on: every write command (run, stop, rm, exec, trigger,
// auth login, ...) is refused before a request is sent. There is no flag that
// turns it off.
package main

import (
	"fmt"
	"io"
	"os"

	"github.com/chaitin/chaitin-cli/products/agentcompose"
	"github.com/spf13/cobra"
)

func main() {
	cmd := agentcompose.NewCommand()
	cmd.Use = "agent-compose-ro"
	prepare := cmd.PersistentPreRunE
	cmd.PersistentPreRunE = func(command *cobra.Command, args []string) error {
		// No config file: URL and token come from AGENT_COMPOSE_* variables.
		agentcompose.ApplyRuntimeConfig(command, nil, "", true)
		return prepare(command, args)
	}
	if err := cmd.Execute(); err != nil {
		if renderer, ok := err.(interface{ RenderError(io.Writer) error }); ok {
			_ = renderer.RenderError(os.Stderr)
		} else {
			fmt.Fprintln(os.Stderr, "Error:", err)
		}
		if coder, ok := err.(interface{ ExitCode() int }); ok {
			os.Exit(coder.ExitCode())
		}
		os.Exit(1)
	}
}
