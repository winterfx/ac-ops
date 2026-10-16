# The image every platform-ops agent runs in: the agent-compose guest image plus
# bin/agent-compose-ro (built by scripts/build-cli.sh), so the read-only CLI
# ships with the image instead of the project mount.
#
# The base is pinned by digest, so a guest image update never changes the
# agents' environment unannounced. To bump it, take the digest of the new
# guest image (`docker image inspect --format '{{.RepoDigests}}' ...`) and
# rebuild with scripts/build-image.sh.
#
# The binary is copied in, not compiled here: devboard cannot reach GitHub or
# the Go module proxy.
ARG BASE=portus.in.chaitin.net/dev/agent-compose-guest@sha256:53c29c05784fb7da06dfbab82d57cbbd3f2523d59238ff98eb3a82622835df54
FROM ${BASE}
ARG CLI_VERSION=unknown
LABEL org.opencontainers.image.title="ac-ops agent" \
      net.chaitin.ac-ops.chaitin-cli="${CLI_VERSION}"
COPY bin/agent-compose-ro /usr/local/bin/agent-compose-ro
RUN agent-compose-ro --help > /dev/null
