// Internal, synthetic-only preview smoke; not a live or canonical model test.
// A separate disposable D1 database with the smoke marker is mandatory.
import {admitRawCapture} from './raw_capture.mjs';
import {prepareCapturedSource} from './source_evidence.mjs';
import {prepareInitialSourcePrompts} from './source_model_prompt.mjs';
import {createVerifiedWorker} from './verified_worker.mjs';

const purpose='private-synthetic-assertion-smoke/v1';
const scope='synthetic-assertion-smoke';
const latency=60_000;
const prompt='The imaginary locker has a blue label.';
const draft=Object.freeze({text:'The imaginary locker has a blue label.',question:null,support_fragments:[0]});
const checked = result => {
  if (result?.success===false || !Array.isArray(result?.results)) throw new Error('Synthetic smoke storage failed.');
  return result.results;
};

export async function runSyntheticStagingSmoke(smokeDb) {
  // Check a separate opt-in marker before any admission, even on an
  // authenticated route. Missing table fails closed without reading raw data.
  try {
    const [probe]=await smokeDb.batch([smokeDb.prepare(
      'SELECT purpose FROM memory_synthetic_smoke_environment WHERE singleton=1 AND purpose=?').bind(purpose)]);
    if (checked(probe)[0]?.purpose!==purpose) throw new Error();
  } catch {
    throw new Error('Dedicated synthetic smoke D1 not configured.');
  }

  const runOne=async accepted => {
    const label=accepted?'accepted':'rejected';
    const fixture={
      session_id:'synthetic-assertion-smoke-v1-'+label,message_id:'fixture-1',actor_id:'user',
      role:'user',text:prompt,position:0,source:'fixture://assertion-smoke/v1',scope,
    };
    const leaseToken=crypto.randomUUID();
    const admitted=await admitRawCapture(smokeDb,fixture);
    async function observe(recorded=false) {
      const [result]=await smokeDb.batch([smokeDb.prepare(`SELECT j.state,j.failure_code,
        (SELECT p.verifier FROM memory_job_publications p WHERE p.job_id=j.id) AS verifier
        FROM memory_jobs j WHERE j.id=? AND (?=0 OR EXISTS (
          SELECT 1 FROM memory_synthetic_smoke_checks c WHERE c.fixture=? AND c.job_id=j.id
        ))`).bind(admitted.event_id,Number(recorded),label)]);
      return checked(result)[0];
    }
    const expected = item => accepted
      ? item?.state==='done' && item.failure_code===null && item.verifier==='source-initial-review'
      : item?.state==='ready' && item.failure_code==='retryable' && item.verifier===null;
    const observation = item => ({
      prepared:true,constructed:true,reviewed:true,
      outcome:accepted?'published':'retrying',queue_state:item.state,staged:item.verifier!==null,
    });
    const replay=await observe(true);
    if (replay && expected(replay)) return observation(replay);
    const now=Date.now();
    // Target this one freshly admitted event. Do not claim any existing job
    // or use an endpoint body to choose a job, a model reply or source bytes.
    const [claimed]=await smokeDb.batch([smokeDb.prepare(`UPDATE memory_jobs SET
      state='running',attempts=1,lease_token=?,lease_expires_at=?,updated_at=?
      WHERE id=? AND kind='source-thoughts' AND state='ready' AND attempts=0 AND available_at<=?
      RETURNING id,kind,payload,payload_sha256,state,lease_token`).bind(
        leaseToken,now+latency,now,admitted.event_id,now)]);
    const job=checked(claimed)[0];
    if (!job) throw new Error('Synthetic preview check is incomplete or already running.');

    let prepared=false, constructed=false, reviewed=false;
    const run=createVerifiedWorker({
      canonicalInitialReview:true,leaseMs:latency,
      prepare(input) {
        if (input.id!==admitted.event_id || input.source!==job.payload) return {eligible:false};
        const source=JSON.parse(input.source);
        if (source.session_id!==fixture.session_id || source.prompt!==fixture.text ||
            source.scope!==fixture.scope || source.source!==fixture.source) return {eligible:false};
        const result=prepareCapturedSource(input);
        prepared=result.eligible && result.fragments.length===1;
        return prepared?result:{eligible:false};
      },
      construct(input) {
        prepareInitialSourcePrompts(input).construct;
        constructed=true;
        return JSON.stringify({thoughts:[draft],needs_detail:null});
      },
      async review(input) {
        prepareInitialSourcePrompts(input).review(input.candidateReply);
        reviewed=true;
        return JSON.stringify(accepted
          ? {verdict:'accept',text:'Scripted test acceptance.'}
          : {verdict:'reject',text:'Scripted test rejection.'});
      },
    });
    const outcome=await run(smokeDb,job);
    const item=await observe();
    if (!prepared || !constructed || !reviewed || !expected(item)
        || outcome.status!==(accepted?'published':'retrying'))
      throw new Error('Synthetic staging produced an unexpected result.');
    const [saved]=await smokeDb.batch([smokeDb.prepare(`INSERT INTO memory_synthetic_smoke_checks
      (fixture,job_id) VALUES (?,?) ON CONFLICT(fixture) DO NOTHING RETURNING fixture`).bind(label,job.id)]);
    if (checked(saved)[0]?.fixture!==label) throw new Error('Synthetic check receipt was not saved.');
    return observation(item);
  };

  const accepted=await runOne(true);
  const rejected=await runOne(false);
  const allStages = item => item.prepared && item.constructed && item.reviewed;
  const ok=allStages(accepted) && accepted.outcome==='published' && accepted.queue_state==='done' && accepted.staged &&
    allStages(rejected) && rejected.outcome==='retrying' && rejected.queue_state==='ready' && !rejected.staged;
  return {ok,kind:'synthetic-staging-smoke',provider_called:false,live_graph_changed:false,accepted,rejected};
}
