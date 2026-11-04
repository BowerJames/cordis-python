from typing import Callable

from cordis import Key

type GetEnv = Callable[[str], str | None]

GET_ENV: Key[GetEnv] = Key("get_env")
