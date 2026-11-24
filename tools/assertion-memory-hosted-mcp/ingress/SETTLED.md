# After the PROD transition has settled

Use this instead of the pre-transition pair planner. Do not rerun the
transition, write to its settled marker, or update its original rollback witness.

1. Under the existing transition fence, verify settled state independently:
   exact settled marker expected, no pending/permit, fallback service + timer
   inactive, active nginx config matching the independently approved settled
   digest. If any mismatch, stop. Do not calculate approval from an unchecked
   active file.
2. Copy the active bytes into a private staging input. Run:

   `python -B stage_nginx_settled.py --settled <private-copy> --expected-settled-sha256 <approved-digest> --new-stage-dir <fresh-private-dir>`

   Existing source and destination parents must be trusted. Run
   `python -B -m unittest test_stage_nginx_settled` in this directory.
3. The output reports the original hash, each untouched side of the splice,
   the exact one-location insert, and zero upstream replacements. Deleting
   that exact fragment restores every original byte. Existing paths, TEST
   fixture, PROD graph and MCP bytes are unchanged. Separately confirm that
   no inherited rule on the relevant TLS virtual host shadows the new route.
4. Validate the proposed configuration with the real setup in a private mount
   namespace and `nginx -t`; stage only if so authorized. Before any separately
   approved active swap, revalidate the original digest under the fence,
   validate again and retain a separate original-byte ingress rollback copy.
   This is not a new PROD transition witness. No settled/permit/rollback
   metadata writes. Verify no-body status responses for exact TEST route and
   existing routes separately; never print credentials or response bodies.
