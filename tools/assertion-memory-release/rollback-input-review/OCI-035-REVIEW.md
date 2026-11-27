# .35 public OCI root: verified input only, not an execution approval

Use the exact platform manifest, not the moving repository tag:
`docker.io/library/ubuntu@sha256:0d5255be560e2d2ddcfbfda88549e9e815ef29e7cd7fc62183d5709e6e2963c6`,
linux/amd64. Docker Official Images maps this dated image to Canonical
Launchpad source `953a97207f60cb0bca021a65d0417b7256f243af`.
Official Docker metadata independently identifies the index, config and layers
listed in base-035-oci-root-pin.json. The public data-byte witness verifies
these inputs; it is not a replay of the original GitHub-hosted runner image.

An authorized acquirer can provide one freshly materialized directory of raw
index, manifest, config and compressed layers named by bare SHA-256. Run:

    python3 verify_035_oci_root.py /absolute/acquired-raw-oci-blobs

The verifier checks the content before any unpack, checks the unique Linux
amd64 reference and all descriptor sizes/digests, never extracts or executes
the image, and returns build_approved=false. Never pass downloaded paths,
cloud keys, runner tokens or candidate-provided selectors into the pin.

The existing offline build/review plan remains default-off. No native runtime
was composed and no cold build executed. Before authorizing the build-only
job, review the exact composed toolchain OCI envelope (existing verified
CPython/Node packages, pinned package material and any required system/ELF
libraries), runner isolation and access; run two fresh network-disabled
builds without lifecycle scripts, checkout credentials, OIDC or deploy
environment. The separate artifacts comparison attests bytes only. Actual
deployment would require independent later authority and is not in this change.

References:
- https://hub.docker.com/layers/library/ubuntu/resolute-20261105.1/images/sha256-0d5255be560e2d2ddcfbfda88549e9e815ef29e7cd7fc62183d5709e6e2963c6
- https://github.com/docker-library/repo-info/blob/master/repos/ubuntu/remote/resolute-20261105.1.md
- https://github.com/docker-library/official-images/blob/master/library/ubuntu
