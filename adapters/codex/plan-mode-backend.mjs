export function createPlanTurn(request, config) {
  const planning = config.planning || {};
  if (!planning.model) throw new Error('planning model is not configured');
  return {
    status: 'ready',
    operation: {
      method: 'turn/start',
      params: {
        threadId: request.sessionRef,
        input: [{ type: 'text', text: request.intent }],
        collaborationMode: {
          mode: 'plan',
          settings: {
            model: planning.model,
            reasoning_effort: planning.reasoningEffort || 'medium',
            developer_instructions: null,
          },
        },
      },
    },
  };
}

const transitions = new Map();

export async function coordinatePlanTransition(decision, context, config, transport) {
  const continuation = decision && decision.continuation;
  const transitionId = continuation && continuation.transitionId;
  if (!transitionId || !continuation.originalIntent || !context || !context.threadId) {
    return { status: 'blocked', blocked: true, reason: 'planning transition lacks identity or original intent' };
  }
  if (!transport || !['denyAction', 'endTurn', 'awaitTurnEnded', 'request'].every((key) => typeof transport[key] === 'function')) {
    return { status: 'unsupported', blocked: true, reason: 'native planning coordinator transport is unavailable' };
  }
  if (transitions.has(transitionId)) return transitions.get(transitionId);
  const run = (async () => {
    try {
      await transport.denyAction(decision);
      await transport.endTurn(context.threadId);
      const ended = await transport.awaitTurnEnded(context.threadId);
      if (!ended || ended.status !== 'ended') throw new Error('current turn did not end');
      const built = createPlanTurn({ sessionRef: context.threadId, intent: continuation.originalIntent }, config);
      const started = await transport.request(built.operation);
      if (!started || started.status !== 'started') throw new Error('planning turn did not start');
      return { status: 'started', blocked: true, transitionId };
    } catch (error) {
      return { status: 'blocked', blocked: true, transitionId, reason: error && error.message ? error.message : 'planning transition failed' };
    }
  })();
  transitions.set(transitionId, run);
  return run;
}
