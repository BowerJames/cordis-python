# Each example's entrypoints, run through uv. Arguments after the recipe name go to the entrypoint, so
# `just chat-tui --provider zai` and `just echo-server --describe` work as the scripts themselves do.

set positional-arguments

# List the recipes.
default:
    @just --list

# An echo server; ECHO_SERVER=fastapi serves it with FastAPI instead.
echo-server *args:
    uv run echo-server "$@"

# A small curl, such as `just curl http://127.0.0.1:8000/echo -d hello -v`.
curl *args:
    uv run curl-example "$@"

# An items API with discovery; SITE_DATABASE=site.sqlite keeps items in SQLite, --routes lists the routes.
items-server *args:
    uv run site-example "$@"

# An agent chat server, an agent per websocket session.
chat-server *args:
    uv run chat-server "$@"

# The chat server with a terminal client of its own; quit it with ctrl+q.
chat-tui *args:
    uv run chat-tui "$@"

# A bot reconfigured while it runs.
bot *args:
    uv run bot-example "$@"
