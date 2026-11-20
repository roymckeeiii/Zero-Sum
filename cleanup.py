"""Facade over the modules cleanup.py was split into, kept so that `cleanup.<name>` still works:

  guard.py      sender validation, keep words, protected senders and own addresses (the refusals)
  collector.py  message metadata into unified.msg
  gmailops.py   filters (recorded), bulk label changes (recorded, guarded), the undo journal
  aisort.py     AI sort passes
  actions.py    sender actions, protection, the Quarantine tab, word plans and previews

Reading a name here gives the object from its module. Assigning a name here (the tests do: `cleanup.gmail = fake`,
`cleanup._batch = ...`) also sets it in every one of those modules that has it, so a function that moved still sees
the replacement. New code should import from the modules directly."""
import sys, types

import actions, aisort, collector, gmailops, guard

_PARTS = (guard, collector, gmailops, aisort, actions)
for _m in _PARTS:
    globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})


class _Facade(types.ModuleType):
    def __setattr__(self, name, value):
        for m in _PARTS:
            if name in vars(m):
                setattr(m, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Facade
