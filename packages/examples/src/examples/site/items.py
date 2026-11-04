import json
from typing import AsyncIterator

from cordis import Component, Context, Inverse
from cordis_plugins import ROUTES, SERVER_ADDRESS, SQL, Route, ServerRequest, ServerResponse

from .contracts import ITEMS, Item, Items


def items_store() -> Component:
    # Items kept in memory, for as long as the component is loaded.
    def apply(ctx: Context) -> None:
        added: dict[int, Item] = {}

        async def add(name: str) -> Item:
            item = Item(id=len(added) + 1, name=name)
            added[item.id] = item
            return item

        async def get(id: int) -> Item | None:
            return added.get(id)

        ctx.provide(ITEMS, Items(add=add, get=get))
    return Component("items store", apply=apply, provides=(ITEMS,))

def sql_items_store() -> Component:
    # The same items, kept in whichever SQL database is provided, in SQL every database shares. The tables are made
    # as it activates if they are not there yet, so a database kept in a file carries its items over to the next run.
    async def apply(ctx: Context) -> AsyncIterator[Inverse | None]:
        sql = ctx.get(SQL)
        async with sql.transaction() as transaction:
            await transaction.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            # The last number given out, in a row of its own: items cannot number themselves portably, and counting
            # them would let two added side by side take the same number.
            await transaction.execute("CREATE TABLE IF NOT EXISTS item_numbers (last INTEGER NOT NULL)")
            # Started from the highest number already used, once: outside the aggregate, as one always gives a row.
            await transaction.execute("INSERT INTO item_numbers (last) SELECT highest FROM "
                                      "(SELECT COALESCE(MAX(id), 0) AS highest FROM items) AS used "
                                      "WHERE NOT EXISTS (SELECT 1 FROM item_numbers)")
        # Making the tables is the database's state, kept on purpose from one run to the next: it is outside what
        # unloading reverts, so this seam has nothing to undo.
        yield None

        async def add(name: str) -> Item:
            # Advancing the counter locks its row until the transaction ends, so items added side by side wait their
            # turn for a number, and each gets one of its own.
            async with sql.transaction() as transaction:
                [row] = await transaction.fetch("UPDATE item_numbers SET last = last + 1 RETURNING last")
                item = Item(id=int(row["last"]), name=name)
                await transaction.execute("INSERT INTO items (id, name) VALUES (:id, :name)", {"id": item.id, "name": name})
            return item

        async def get(id: int) -> Item | None:
            rows = await sql.fetch("SELECT id, name FROM items WHERE id = :id", {"id": id})
            return Item(id=rows[0]["id"], name=rows[0]["name"]) if rows else None

        ctx.provide(ITEMS, Items(add=add, get=get))

    return Component("sql items store", apply=apply, requires=(SQL,), provides=(ITEMS,))

def serve_route(ctx: Context, route: Route) -> None:
    # A route is an effect of the component adding it, withdrawn as it unloads.
    ctx.effect(lambda: ctx.get(ROUTES).add(route), f"route {route}")

def items_api() -> Component:
    # Adds its routes, and requires the items they serve: give it another store and it reloads against the new one, by
    # itself. The server's address is read as each item is created, from whichever server is answering, so the routes
    # are in the table whether or not any server is loaded.
    def apply(ctx: Context) -> None:
        async def add(request: ServerRequest) -> ServerResponse:
            try:
                name = json.loads(request.body)["name"]
            except (ValueError, KeyError, TypeError):
                return ServerResponse.json({"error": "send {\"name\": ...}"}, status=400)
            item = await ctx.get(ITEMS).add(str(name))
            created = ServerResponse.json({"id": item.id, "name": item.name}, status=201)
            address = ctx.find(SERVER_ADDRESS)
            location = f"{address or ''}/items/{item.id}"
            return ServerResponse(status=201, headers=(*created.headers, ("Location", location)), body=created.body)

        async def get(request: ServerRequest) -> ServerResponse:
            wanted = request.params["id"]
            item = await ctx.get(ITEMS).get(int(wanted)) if wanted.isdigit() else None
            if item is None:
                return ServerResponse.json({"error": "no such item"}, status=404)
            return ServerResponse.json({"id": item.id, "name": item.name})

        for route in (Route("POST", "/items", add), Route("GET", "/items/{id}", get)):
            serve_route(ctx, route)

    return Component("items api", apply=apply, requires=(ROUTES, ITEMS))
