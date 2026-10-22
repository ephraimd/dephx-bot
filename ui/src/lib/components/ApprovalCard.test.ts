import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';
import ApprovalCard from './ApprovalCard.svelte';
import type { Decision } from '#lib/events.js';
import type { ApprovalItem } from '#lib/transcript.svelte.js';

let card: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (card) unmount(card);
	card = null;
	document.body.innerHTML = '';
});

const change: ApprovalItem = {
	kind: 'approval',
	key: 't1:approval:a1',
	approvalId: 'a1',
	approvalKind: 'settings',
	text: 'Commands that run without asking:\n+ uv run pytest',
	decision: null,
	expired: false
};

function buttons(): string[] {
	return [...document.querySelectorAll('button')].map((b) => b.textContent?.trim() ?? '');
}

describe('ApprovalCard', () => {
	it('shows a settings change in full with Apply and Deny', async () => {
		const answers: Decision[] = [];
		card = mount(ApprovalCard, {
			target: document.body,
			props: { item: change, onAnswer: async (decision: Decision) => void answers.push(decision) }
		});
		flushSync();

		expect(document.body.textContent).toContain('+ uv run pytest');
		expect(buttons()).toEqual(['Apply', 'Deny']);
		document.querySelector('button')!.click();
		await Promise.resolve();
		expect(answers).toEqual(['approve']);
	});

	it('says what happened to a settings change once answered', () => {
		card = mount(ApprovalCard, {
			target: document.body,
			props: { item: { ...change, decision: 'deny' }, onAnswer: async () => {} }
		});
		flushSync();
		expect(document.body.textContent).toContain('You kept the settings as they were.');
	});
});
