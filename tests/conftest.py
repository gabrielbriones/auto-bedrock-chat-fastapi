"""TEMPORARY diagnostic instrumentation for the Python-3.10-only CI failure
(AttributeError: module 'autolangchat' has no attribute 'graph'/'model_capabilities').

Traces every import of these two submodules: which file/line triggered it,
whether it was a cache hit, and whether the parent package ends up with the
attribute bound. Remove once root-caused.
"""

import builtins
import sys
import traceback

_TRACE_NAMES = {"autolangchat.graph", "autolangchat.model_capabilities"}
_orig_import = builtins.__import__


def _traced_import(name, globals=None, locals=None, fromlist=(), level=0):
    was_cached = name in sys.modules
    result = _orig_import(name, globals, locals, fromlist, level)
    if name in _TRACE_NAMES:
        parent_name = name.rsplit(".", 1)[0]
        attr_name = name.rsplit(".", 1)[1]
        parent = sys.modules.get(parent_name)
        has_attr = hasattr(parent, attr_name)
        parent_id = id(parent) if parent is not None else None
        caller = "".join(traceback.format_stack(limit=4)[:-1]).replace("\n", " | ")
        print(
            f"\n[TRACE-IMPORT] name={name} was_cached_before={was_cached} "
            f"parent_id={parent_id} has_attr_after={has_attr} caller={caller}\n"
        )
    return result


builtins.__import__ = _traced_import
