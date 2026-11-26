# Deployment-only integration

Base and Visual publication, tag creation and ordinary artifact building are
unmodified. There is no publisher, ruleset, or build hook to activate.

At entry to the **existing** protected TEST deployment, independently verify
the immutable release payload. Under the family lock, use fresh evidence of
the preceding major actually running on PROD (or a verified defect-only review
of the currently open major's failed TEST). Record one forward train and only
then switch TEST. A later major may already have an immutable built artifact;
this does not grant an activation or affect any already-eligible promotion.

At existing PROD, retain the existing stage approval and verify the exact
artifact and actual TEST generation. Reserve one promotion before effects.
Record closure only after the real running PROD is verified. No automatic
unlock or retry after uncertain effects. Supervised 0.1.x current in-flight
releases, urgent gateway support and privileged rollback do not enter the
forward train and never close it.

Adapter code accepts only protected evidence callbacks. These must be wired
from the existing receiver and not caller payload. Filesystem lock and state
root require deliberate receiver installation; neither active environment
changes in this draft.
