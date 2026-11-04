# Each example's entrypoints, run through uv. Arguments after the recipe name go to the entrypoint, so
# `just chat-tui --provider zai` and `just echo-server --describe` work as the scripts themselves do.

set positional-arguments

# Chat's own .env, loaded into its environment by uv when the file is there. A variable already set in the shell keeps
# its value over the file's.
chat_env := justfile_directory() / "examples" / "chat" / ".env"
chat_env_file := if path_exists(chat_env) == "true" { "--env-file " + quote(chat_env) } else { "" }

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

# An agent chat server, an agent per websocket session, with examples/chat/.env loaded if there is one.
chat-server *args:
    uv run {{chat_env_file}} chat-server "$@"

# The chat server with a terminal client of its own, with examples/chat/.env loaded if there is one; quit with ctrl+q.
chat-tui *args:
    uv run {{chat_env_file}} chat-tui "$@"

# A bot reconfigured while it runs.
bot *args:
    uv run bot-example "$@"
