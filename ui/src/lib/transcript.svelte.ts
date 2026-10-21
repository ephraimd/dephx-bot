import type { ChatState } from './api';
import type { AgentEvent, Decision, Phase, TurnStatus, UsageReported } from './events';

export type UserItem = { kind: 'user'; key: string; text: string };
export type AssistantItem = {
	kind: 'assistant';
	key: string;
	step: number;
	text: string;
	usage: UsageReported | null;
};
export type ToolItem = {
	kind: 'tool';
	key: string;
	callId: string;
	name: string;
	summary: string;
	state: 'running' | 'done' | 'failed';
	output: string;
	addedTokens: number;
};
export type ApprovalItem = {
	kind: 'approval';
	key: string;
	approvalId: string;
	approvalKind: 'command' | 'plan';
	text: string;
	decision: Decision | null;
	expired: boolean;
};
export type PhaseItem = { kind: 'phase'; key: string; phase: Phase; summary: string };
export type NoticeItem = {
	kind: 'notice';
	key: string;
	status: TurnStatus | 'error';
	code: string | null;
	text: string;
};
export type Item = UserItem | AssistantItem | ToolItem | ApprovalItem | PhaseItem | NoticeItem;

// The turn_finished statuses that need words of their own; the rest use the server's message.
const STATUS_TEXT: Partial<Record<TurnStatus, string>> = {
	done_verified: 'Finished and verified',
	done_unverified: 'Finished, not verified',
	waiting_for_user: 'Waiting for your reply',
	incomplete: 'Stopped before finishing. Send a message to continue.',
	stopped: 'Stopped',
	step_limit: 'Step limit reached'
};

/** The chat as the screen shows it, rebuilt from the turn's events. The only place events are read. */
export class Transcript {
	items: Item[] = $state([]);
	phase: Phase = $state('evaluate');
	running = $state(false);
	chatUsed = $state(0);
	chatLimit = $state(0);
	contextTokens = $state(0);
	contextBudget = $state(0);

	#turnId = '';
	#errorShown = false;

	sync(state: ChatState): void {
		this.phase = state.phase;
		this.chatUsed = state.chat_used;
		this.chatLimit = state.chat_limit;
		this.contextTokens = state.context_tokens;
		this.contextBudget = state.context_budget;
	}

	clear(): void {
		this.items = [];
		this.running = false;
	}

	startTurn(turnId: string, userText: string | null): void {
		this.#turnId = turnId;
		this.#errorShown = false;
		this.running = true;
		if (userText !== null) this.items.push({ kind: 'user', key: `${turnId}:user`, text: userText });
	}

	apply(event: AgentEvent): void {
		switch (event.type) {
			case 'token':
				this.#reply(event.step).text += event.text;
				break;
			case 'usage':
				this.#reply(event.step).usage = event;
				this.chatUsed = event.chat_used;
				this.chatLimit = event.chat_limit;
				this.contextTokens = event.context_tokens;
				this.contextBudget = event.context_budget;
				break;
			case 'tool_started':
				this.items.push({
					kind: 'tool',
					key: `${this.#turnId}:${event.call_id}`,
					callId: event.call_id,
					name: event.name,
					summary: event.summary,
					state: 'running',
					output: '',
					addedTokens: 0
				});
				break;
			case 'tool_finished': {
				const tool = this.items.find(
					(item): item is ToolItem => item.kind === 'tool' && item.callId === event.call_id
				);
				if (tool) {
					tool.state = event.ok ? 'done' : 'failed';
					tool.output = event.output;
					tool.addedTokens = event.added_tokens;
				}
				break;
			}
			case 'approval_required':
				this.items.push({
					kind: 'approval',
					key: `${this.#turnId}:${event.approval_id}`,
					approvalId: event.approval_id,
					approvalKind: event.kind,
					text: event.text,
					decision: null,
					expired: false
				});
				break;
			case 'approval_resolved': {
				const approval = this.items.find(
					(item): item is ApprovalItem => item.kind === 'approval' && item.approvalId === event.approval_id
				);
				if (approval) approval.decision = event.decision;
				break;
			}
			case 'phase_changed':
				this.phase = event.phase;
				this.items.push({
					kind: 'phase',
					key: `${this.#turnId}:phase:${this.items.length}`,
					phase: event.phase,
					summary: event.summary
				});
				break;
			case 'error':
				this.#errorShown = true;
				this.#notice('error', event.code, event.message);
				break;
			case 'turn_finished':
				this.running = false;
				// The server dropped any question still open; its buttons would only get a 404 now.
				for (const item of this.items) {
					if (item.kind === 'approval' && item.decision === null) item.expired = true;
				}
				if (event.status === 'failed' && this.#errorShown) break;
				this.#notice(event.status, null, this.#statusText(event.status, event.message));
				break;
		}
	}

	#statusText(status: TurnStatus, message: string): string {
		if (status === 'token_limit') {
			const used = this.chatUsed.toLocaleString('en-US');
			const limit = this.chatLimit.toLocaleString('en-US');
			return `Token limit reached: ${used} of ${limit} used. Raise the limit for this chat to continue.`;
		}
		return STATUS_TEXT[status] ?? message;
	}

	#notice(status: NoticeItem['status'], code: string | null, text: string): void {
		this.items.push({ kind: 'notice', key: `${this.#turnId}:notice:${this.items.length}`, status, code, text });
	}

	#reply(step: number): AssistantItem {
		const key = `${this.#turnId}:step:${step}`;
		const existing = this.items.find((item): item is AssistantItem => item.key === key);
		if (existing) return existing;
		this.items.push({ kind: 'assistant', key, step, text: '', usage: null });
		return this.items.at(-1) as AssistantItem;
	}
}
