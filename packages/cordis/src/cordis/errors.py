# Each is a precondition the paper places on an operation: violating it raises, and no transition happens.

class CordisError(Exception):
    pass

class UndeclaredAccess(CordisError):
    # Reading a key no fiber up the chain declared (Algorithm 6): the coeffect specification is the capability.
    pass

class InactiveAccess(CordisError):
    # Reading a key that is declared but not committed: the fiber is not activated against a provider of it.
    pass

class ProvisionError(CordisError):
    # Providing a key outside the component's provision (Definition 48), or one already bound in its realm, which
    # set's precondition k ∉ dom(σ) forbids (Definition 20).
    pass

class InsertionError(CordisError):
    # O-Insert refused: a provision overlapping another fiber's in the same realm, so a key would have two providers.
    pass

class InactiveEffect(CordisError):
    # An effect performed on a context whose fiber is neither activating nor active, so nothing would ever revert it.
    pass
