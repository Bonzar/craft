# Base .35 unsigned full-pair qualification

Start from immutable Release ID 459435759, tag assertion-memory-v0.1.35,
commit 710cf557d29cf91e6ab1550d27e525817df96588. Use the previously reviewed
Base-v1 rollback packer/qualifier from draft #333 (base 15cddf2), retained
under this inert review directory. The only historical packaging exception
is the exact browser-listener contract marker present in that Git tree;
both assembly and full receiver validation require its pinned Git blob.

Two independent fresh hosted replicas revalidate public dependencies,
compile Graph and catalogue in the previously qualified offline Docker
context, and each assemble/validate a full Base-v1 payload in private
temporary storage. Only source identity, graph/catalogue archive SHA-256,
full file manifest SHA-256, canonical compressed archive SHA-256 and
count cross runners as job output scalars. The third job compares them;
a local mechanical fixture additionally compares the manifests and all
compressed bytes directly. No workflow artifact, manifest rows, package
bytes, or archive are published, and nothing signs or deploys.

The reproducibility question is whether the two new packages have the
same qualified content and canonical byte representation. The original
release shell used GNU tar over a staged directory, explicit directory
entries and its Git/archive-derived modes and SHA256SUMS naming. The
current trusted draft packer has a fixed sorted-file USTAR representation,
mode 0644, zero timestamps and deterministic gzip; it is receiver-qualified
but its representation need not reproduce the original deployed archive's
checksum. Historical rollback still requires its separate artifact origin
and unchanged protected sealing/verifier path; an unsigned digest match is
not an original-release byte witness, authorization or deploy permit.

This YAML is outside .github/workflows and all jobs are hard-off. After
separate review, prospective installation must keep protected main only,
first manual attempt, no deployment environments/write permissions, and
must not enable the signing/receiver path. Running the original historical
workflow directly is not hermetic: its pip/npm installs use their ordinary
online path. The fixed current materializer + offline installation is the
reviewed candidate, not a reproduction claim until it actually runs.
