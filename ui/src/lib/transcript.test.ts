import { describe, expect, it } from 'vitest';
import type { AgentEvent, UsageReported } from './events';
import { Transcript } from './transcript.svelte';

function usage(step: number, chatUsed = 1_000): UsageReported {
	return {
		type: 'usage',
		step,
		input_tokens: 900,
		output_tokens: 100,
		cached_tokens: null,
		estimated: false,
		cost: 0.0004,
		currency: 'ESP',
		chat_used: chatUsed,
		chat_limit: 300_000,
		context_tokens: 4_000,
		context_budget: 54_344
	};
}

function play(transcript: Transcript, events: AgentEvent[]): void {
	events.forEach((event) => transcript.apply(event));
}

describe('Transcript', () => {
	it('starts a turn with the user message', () => {
		const t = new Transcript();
		t.startTurn('t1', 'add a test');
		expect(t.items).toEqual([{ kind: 'user', key: 't1:user', text: 'add a test' }]);
		expect(t.running).toBe(true);
	});

	it('joins streamed text into one reply per step and attaches its usage', () => {
		const t = new Transcript();
		t.startTurn('t1', 'hi');
		play(t, [
			{ type: 'token', step: 1, text: 'Hel' },
			{ type: 'token', step: 1, text: 'lo' },
			usage(1, 1_000)
		]);

		const reply = t.items[1];
		expect(reply).toMatchObject({ kind: 'assistant', text: 'Hello' });
		expect(reply.kind === 'assistant' && reply.usage?.input_tokens).toBe(900);
		expect(t.chatUsed).toBe(1_000);
		expect(t.contextBudget).toBe(54_344);
	});

	it('gives a tool-only step a reply holding just its usage', () => {
		const t = new Transcript();
		t.startTurn('t1', 'go');
		play(t, [usage(1), { type: 'tool_started', call_id: 'c1', name: 'read_file', summary: 'a.py' }]);
		expect(t.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'tool']);
	});

	it('tracks a tool from running to finished', () => {
		const t = new Transcript();
		t.startTurn('t1', 'go');
		play(t, [
			{ type: 'tool_started', call_id: 'c1', name: 'run_command', summary: 'pytest' },
			{ type: 'tool_finished', call_id: 'c1', ok: false, output: 'exit code 1', added_tokens: 5 }
		]);
		expect(t.items[1]).toMatchObject({ kind: 'tool', state: 'failed', output: 'exit code 1', addedTokens: 5 });
	});

	it('records approvals and their answers', () => {
		const t = new Transcript();
		t.startTurn('t1', 'go');
		play(t, [
			{ type: 'approval_required', approval_id: 'a1', kind: 'command', text: 'pytest' },
			{ type: 'approval_resolved', approval_id: 'a1', decision: 'run_once' }
		]);
		expect(t.items[1]).toMatchObject({ kind: 'approval', approvalId: 'a1', decision: 'run_once' });
	});

	it('closes questions left unanswered when the turn ends', () => {
		const t = new Transcript();
		t.startTurn('t1', 'go');
		play(t, [
			{ type: 'approval_required', approval_id: 'a1', kind: 'command', text: 'pytest' },
			{ type: 'turn_finished', status: 'stopped', message: 'Stopped.' }
		]);
		expect(t.items[1]).toMatchObject({ kind: 'approval', decision: null, expired: true });
	});

	it('follows the phase', () => {
		const t = new Transcript();
		t.startTurn('t1', 'go');
		play(t, [{ type: 'phase_changed', phase: 'build', summary: '1. write it' }]);
		expect(t.phase).toBe('build');
		expect(t.items[1]).toMatchObject({ kind: 'phase', phase: 'build' });
	});

	it('ends the turn with a notice', () => {
		const t = new Transcript();
		t.startTurn('t1', 'go');
		play(t, [{ type: 'turn_finished', status: 'done_unverified', message: 'Finished, not verified.' }]);
		expect(t.running).toBe(false);
		expect(t.items[1]).toMatchObject({ kind: 'notice', status: 'done_unverified', text: 'Finished, not verified' });
	});

	it('shows a provider error once, not again as a failed turn', () => {
		const t = new Transcript();
		t.startTurn('t1', 'go');
		play(t, [
			{ type: 'error', code: 'model_unavailable', message: 'Praxiom is busy.' },
			{ type: 'turn_finished', status: 'failed', message: 'Praxiom is busy.' }
		]);
		const notices = t.items.filter((i) => i.kind === 'notice');
		expect(notices).toEqual([expect.objectContaining({ status: 'error', code: 'model_unavailable', text: 'Praxiom is busy.' })]);
	});

	it('explains a token limit with the real numbers', () => {
		const t = new Transcript();
		t.startTurn('t1', 'go');
		play(t, [usage(1, 290_000), { type: 'turn_finished', status: 'token_limit', message: 'x' }]);
		const notice = t.items.at(-1);
		expect(notice).toMatchObject({ kind: 'notice', status: 'token_limit' });
		expect(notice?.kind === 'notice' && notice.text).toBe(
			'Token limit reached: 290,000 of 300,000 used. Raise the limit for this chat to continue.'
		);
	});
});
