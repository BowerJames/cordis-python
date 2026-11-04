from cordis import Component, Context, Key, Runtime

DB: Key[str] = Key("db")
GREET: Key[str] = Key("greet")
URL: Key[str] = Key("url")

def made(name: str, requires: tuple[Key[str], ...] = (), provides: tuple[Key[str], ...] = ()) -> Component:
    def apply(ctx: Context) -> None:
        for key in provides:
            ctx.provide(key, name)
    return Component(name, apply=apply, requires=requires, provides=provides)


async def test_describe_gives_the_activation_order_with_its_reasons(runtime: Runtime) -> None:
    # Inserted in an order nothing could boot in, and described before anything has run.
    runtime.root.use(made("app", requires=(DB, GREET)))
    runtime.root.use(made("pages", requires=(DB,)))
    runtime.root.use(made("english", provides=(GREET,)))
    runtime.root.use(made("postgres", provides=(DB,)))
    assert runtime.describe() == """\
Activation order (unloaded in reverse):
  1. english
  2. postgres
  3. app      after postgres (db), english (greet)
  4. pages    after postgres (db)

Fibers:
  app       inactive
  pages     inactive
  english   loading
  postgres  loading

Keys:
  db     provided by postgres; required by app, pages
  greet  provided by english; required by app"""
    await runtime.settled()
    assert "app       active" in runtime.describe()

async def test_describe_names_what_keeps_fibers_from_activating(runtime: Runtime) -> None:
    runtime.root.use(made("router", requires=(GREET,), provides=(URL,)))
    runtime.root.use(made("pages", requires=(URL,), provides=(GREET,)))
    runtime.root.use(made("site", requires=(DB,)))
    await runtime.settled()
    assert runtime.describe() == """\
Problems:
  - site requires db, which nothing provides
  - router -[greet]-> pages -[url]-> router form a cycle, so none of them can activate; split one so what it \
provides is separate from what it requires

Fibers:
  router  inactive
  pages   inactive
  site    inactive

Keys:
  url    provided by router; required by pages
  greet  provided by pages; required by router
  db     not provided; required by site"""

async def test_describe_tells_apart_keys_and_fibers_that_share_a_name(runtime: Runtime) -> None:
    other_db: Key[str] = Key("db")
    runtime.root.use(made("store", provides=(DB,)))
    runtime.root.use(made("store", provides=(other_db,)))
    runtime.root.use(made("app", requires=(DB, other_db)))
    description = runtime.describe()
    assert "3. app     after store#1 (db#1), store#2 (db#2)" in description
    assert "db#2  provided by store#2; required by app" in description

async def test_describe_shows_the_fiber_tree_realms_and_failures(runtime: Runtime) -> None:
    def broken(ctx: Context) -> None:
        raise RuntimeError("no port")

    def group(ctx: Context) -> None:
        ctx.isolate(DB).use(made("tenant db", provides=(DB,)))
        ctx.use(Component("server", apply=broken))

    runtime.root.use(Component("group", apply=group))
    await runtime.settled()
    description = runtime.describe()
    assert """\
Fibers:
  group        active
    tenant db  active
    server     failed: no port""" in description
    assert "db in group  provided by tenant db" in description

async def test_an_empty_runtime_says_so(runtime: Runtime) -> None:
    assert runtime.describe() == "Nothing is registered."
