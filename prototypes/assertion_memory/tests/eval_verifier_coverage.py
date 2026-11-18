"""Calibrate the verifier on bundled invented cases; model calls require --execute."""
import argparse
import json
from pathlib import Path

from ..mcp_server import ServiceConfig
from ..model import ModelRequest
from ..storage import MemoryStore
from ..thought_engine_contract import VERIFY, validate_response
from ..thought_schema import response_schema
from ..thought_support import prepare_support
from ..thought_trace import ThoughtTrace

FIXTURE = Path(__file__).with_name('fixtures') / 'verifier_coverage.json'


def load_cases():
    cases = json.loads(FIXTURE.read_text())
    required = {'name', 'question', 'records', 'bases', 'text', 'expected'}
    if not isinstance(cases, list) or not cases:
        raise ValueError('verifier fixture must contain cases')
    for case in cases:
        if (not isinstance(case, dict) or not required <= set(case)
                or set(case) - required - {'support_fragments'} or case['expected'] not in ('accept', 'reject')):
            raise ValueError('invalid verifier case')
        for key in ('name', 'question', 'text'):
            if not isinstance(case[key], str) or not case[key].strip():
                raise ValueError('verifier case requires nonempty ' + key)
        catalog = prepare_support(case['records'], case['bases'])
        if 'support_fragments' in case:
            catalog.resolve(case['support_fragments'])
    if len({case['name'] for case in cases}) != len(cases):
        raise ValueError('duplicate verifier case name')
    return cases


def evaluate(output, cases):
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = output / 'review.sqlite3'
    results = []
    with MemoryStore(path) as memory:
        model = ServiceConfig(db=str(path), provider='codex', model='gpt-6.2-sol',
                              review_model='gpt-6.2-sol', reasoning_effort='low').components(memory)[0]
        for case in cases:
            catalog = prepare_support(case['records'], case['bases'])
            selected = case.get('support_fragments', list(range(len(catalog.fragments))))
            draft = {'role': 'draft', 'text': case['text'], 'question': case['question'],
                     'support_fragments': selected, 'origin': {'kind': 'inference'}}
            request = ModelRequest('Verify thought', [*catalog.selected_packet([selected]), draft], VERIFY,
                response_schema=response_schema('Verify thought'))
            trace = ThoughtTrace(memory, 'synthetic_verifier_calibration', {'case': case['name']})
            response = json.loads(trace.call(model, request, case['bases']))
            validate_response('Verify thought', response)
            trace.finish()
            result = {'case': case['name'], 'expected': case['expected'], 'response': response,
                      'passed': response['verdict'] == case['expected'], 'calls': trace.calls()}
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k != 'calls'}), flush=True)
    (output / 'report.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return all(row['passed'] for row in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    cases = load_cases()
    if not args.execute:
        print(f'{len(cases)} synthetic cases validated; no model calls made.')
        return
    if args.output is None:
        parser.error('--execute requires a new --output directory')
    raise SystemExit(not evaluate(args.output, cases))


if __name__ == '__main__':
    main()
