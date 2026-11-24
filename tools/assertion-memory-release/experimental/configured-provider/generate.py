from pathlib import Path

root = Path(__file__).parent
s = """name: Configured Release
on:
  workflow_call:
    inputs:
      profile: {type: string, required: true}
      tag: {type: string, required: true}
    secrets:
"""
keys = ['HOST_PIN','SSH_KEY','SIGNING_KEY','GATEWAY_TOKEN']
for k in keys: s+=f'      CONFIGURED_VISUAL_{k}: {{required: true}}\n'
s+="""permissions: {}
jobs:
  build:
    name: Build
    if: github.repository == 'Bonzar/craft' && github.ref == 'refs/heads/main' && github.ref_protected && github.run_attempt == '1'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
      actions: read
    outputs:
"""
out = ["tag","revision","release_id","build_run_id","artifact_id","archive_sha"]
for k in out: s+=f"      {k}: ${{{{ steps.resolve.outputs.{k} }}}}\n"
s+="""    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          ref: ${{ github.sha }}
          persist-credentials: false
      - id: resolve
        env:
          PROFILE: ${{ inputs.profile }}
          SELECTED_TAG: ${{ inputs.tag }}
          GH_TOKEN: ${{ github.token }}
          CONTROL_REF_PROTECTED: ${{ github.ref_protected }}
        run: PYTHONPATH=tools/assertion-memory-release python3 -B tools/assertion-memory-release/experimental/configured_release.py
"""
for stage in ["test","prod"]:
    s+=f"""  {stage}:
    name: {stage.upper()}
    needs: {'[build]' if stage == 'test' else '[build, test]'}
    if: inputs.profile == 'visual' && vars.CONFIGURED_VISUAL_GLOBAL_ENABLE == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 13
    environment: configured-release-visual-{stage}
    concurrency:
      group: assertion-memory-visual-{stage}
      cancel-in-progress: false
    permissions:
      contents: read
      actions: read
    env:
      VISUAL_ENV: {stage}
      GRAPH_TAG: ${{{{ needs.build.outputs.tag }}}}
      NATIVE_VISUAL_READY: ${{{{ vars.CONFIGURED_VISUAL_READY }}}}
"""
    if stage=='test': s+="""    outputs:
      proof: ${{ steps.receipt.outputs.proof }}
"""
    s+="""    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          ref: ${{ github.sha }}
          persist-credentials: false
      - name: Set local directory
        run: printf 'VISUAL_DOWNLOAD=%s/configured-visual\\n' "$RUNNER_TEMP" >> "$GITHUB_ENV"
      - id: artifact
        name: Verify exact immutable visual archive and stage approval
        env:
          GH_TOKEN: ${{ github.token }}
          NATIVE_VISUAL_ENV_ID: ${{ vars.CONFIGURED_VISUAL_ENV_ID }}
          NATIVE_VISUAL_REVIEWER_ID: ${{ vars.CONFIGURED_VISUAL_REVIEWER_ID }}
"""
    for k in out[1:]: s+=f"          GRAPH_{k.upper()}: ${{{{ needs.build.outputs.{k} }}}}\n"
    s+="          GRAPH_VERIFIED_SHA: ${{ needs.build.outputs.archive_sha }}\n"
    if stage=='prod': s+="          NATIVE_VISUAL_TEST_PROOF: ${{ needs.test.outputs.proof }}\n"
    s+="""        run: PYTHONPATH=tools/assertion-memory-release python3 -B tools/assertion-memory-visual/configured_visual.py resolve
      - name: Activate exact visual archive
        env:
          VISUAL_REVISION: ${{ steps.artifact.outputs.revision }}
          VISUAL_SHA: ${{ steps.artifact.outputs.sha256 }}
          VISUAL_DIGEST: ${{ steps.artifact.outputs.release }}
          CONFIGURED_VISUAL_DEPLOY_ENABLED: ${{ vars.CONFIGURED_VISUAL_DEPLOY_ENABLED }}
          VISUAL_HOST: ${{ vars.CONFIGURED_VISUAL_HOST }}
          VISUAL_PORT: ${{ vars.CONFIGURED_VISUAL_PORT }}
"""
    for k in keys[:3]: s+=f"          VISUAL_{k}: ${{{{ secrets.CONFIGURED_VISUAL_{k} }}}}\n"
    s+="""        run: PYTHONPATH=tools/assertion-memory-release python3 -B tools/assertion-memory-visual/configured_visual.py send
      - name: Smoke exact visual ingress
        env:
          VISUAL_DIGEST: ${{ steps.artifact.outputs.release }}
          VISUAL_GATEWAY_HOST: ${{ vars.CONFIGURED_VISUAL_GATEWAY_HOST }}
          VISUAL_GATEWAY_TOKEN: ${{ secrets.CONFIGURED_VISUAL_GATEWAY_TOKEN }}
        run: PYTHONPATH=tools/assertion-memory-release python3 -B tools/assertion-memory-visual/smoke.py
"""
    if stage=='test':
        s+="""      - name: Record exact native visual TEST identity
        env:
          NATIVE_VISUAL_TEST_PROOF: ${{ steps.artifact.outputs.proof }}
        run: PYTHONPATH=tools/assertion-memory-release python3 -B tools/assertion-memory-visual/configured_visual.py receipt
      - name: Preserve visual TEST evidence
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: configured-release-visual-test-${{ github.run_id }}-attempt-${{ github.run_attempt }}
          path: ${{ runner.temp }}/configured-visual/native-test-proof.json
          if-no-files-found: error
      - id: receipt
        name: Publish same-run visual TEST proof for PROD
        env:
          PROOF: ${{ steps.artifact.outputs.proof }}
        run: printf 'proof=%s\\n' "$PROOF" >> "$GITHUB_OUTPUT"
"""
(root/'configured-release-reusable.yml.template').write_text(s)
s="""name: Configured Release
on:
  workflow_dispatch:
    inputs:
      profile:
        type: choice
        options: [visual]
        required: true
      tag: {type: string, required: true}
permissions: {}
jobs:
  configured:
    if: github.repository == 'Bonzar/craft' && github.ref == 'refs/heads/main' && github.ref_protected && github.run_attempt == '1'
    permissions:
      contents: read
      actions: read
    uses: ./.github/workflows/configured-release-reusable.yml
    with:
      profile: ${{ inputs.profile }}
      tag: ${{ inputs.tag }}
    secrets:
"""
for k in keys: s+=f"      CONFIGURED_VISUAL_{k}: ${{{{ secrets.CONFIGURED_VISUAL_{k} }}}}\n"
(root/'configured-release.yml.template').write_text(s)
