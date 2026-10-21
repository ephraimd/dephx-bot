// Mirrors praxium/agent/events.py field for field. Change both together.

export type Phase = 'evaluate' | 'ideate' | 'build' | 'test' | 'done';

export type TurnStatus =
	| 'done_verified'
	| 'done_unverified'
	| 'waiting_for_user'
	| 'incomplete'
	| 'stopped'
	| 'step_limit'
	| 'token_limit'
	| 'failed';

export type Decision = 'run_once' | 'always' | 'deny' | 'approve' | 'request_changes';

export type PhaseChanged = { type: 'phase_changed'; phase: Phase; summary: string };
export type TokenDelta = { type: 'token'; step: number; text: string };
export type UsageReported = {
	type: 'usage';
	step: number;
	input_tokens: number;
	output_tokens: number;
	cached_tokens: number | null;
	estimated: boolean;
	cost: number | null;
	currency: string | null;
	chat_used: number;
	chat_limit: number;
	context_tokens: number;
	context_budget: number;
};
export type ToolStarted = { type: 'tool_started'; call_id: string; name: string; summary: string };
export type ToolFinished = {
	type: 'tool_finished';
	call_id: string;
	ok: boolean;
	output: string;
	added_tokens: number;
};
export type ApprovalRequired = {
	type: 'approval_required';
	approval_id: string;
	kind: 'command' | 'plan';
	text: string;
};
export type ApprovalResolved = { type: 'approval_resolved'; approval_id: string; decision: Decision };
export type Compacted = {
	type: 'compacted';
	automatic: boolean;
	before_tokens: number;
	after_tokens: number;
	summary: string;
};
export type TurnFinished = { type: 'turn_finished'; status: TurnStatus; message: string };
export type ErrorRaised = { type: 'error'; code: string; message: string };

export type AgentEvent =
	| PhaseChanged
	| TokenDelta
	| UsageReported
	| ToolStarted
	| ToolFinished
	| ApprovalRequired
	| ApprovalResolved
	| Compacted
	| TurnFinished
	| ErrorRaised;
