import type { AgentEvent, ApprovalRequired, Phase } from './events';

const TOKEN_KEY = 'dephx-bot-launch-token';
let token = '';

/**
 * Read the launch token from the URL fragment, then remove it from the address.
 * sessionStorage keeps it for reloads of this window; it dies with the window, and the
 * random port gives every launch its own storage.
 */
export function initToken(): void {
	const match = location.hash.match(/token=([^&]+)/);
	if (match) {
		sessionStorage.setItem(TOKEN_KEY, match[1]);
		history.replaceState(null, '', location.pathname);
	}
	token = sessionStorage.getItem(TOKEN_KEY) ?? '';
}

export class ApiFailure extends Error {
	constructor(
		readonly status: number,
		readonly code: string,
		message: string
	) {
		super(message);
	}
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
	const response = await fetch(path, {
		method,
		headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (!response.ok) {
		const error = await response.json().catch(() => null);
		throw new ApiFailure(
			response.status,
			error?.error?.code ?? 'network_error',
			error?.error?.message ?? `Request failed (${response.status}).`
		);
	}
	return response.json() as Promise<T>;
}

export async function imageUrl(path: string): Promise<string> {
	const response = await fetch(`/api/project/image?path=${encodeURIComponent(path)}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (!response.ok) throw new ApiFailure(response.status, 'image_failed', `Could not load ${path}.`);
	return URL.createObjectURL(await response.blob());
}

/** Split a buffer of server-sent events into parsed events and the unfinished remainder. */
export function parseFrames(buffer: string): { events: AgentEvent[]; rest: string } {
	const frames = buffer.split('\n\n');
	const rest = frames.pop() ?? '';
	const events = frames
		.flatMap((frame) => frame.split('\n'))
		.filter((line) => line.startsWith('data: '))
		.map((line) => JSON.parse(line.slice('data: '.length)) as AgentEvent);
	return { events, rest };
}

/** Follow one turn's events. EventSource cannot send the token header, so this reads the stream with fetch. */
export async function streamTurn(
	turnId: string,
	onEvent: (event: AgentEvent) => void,
	signal: AbortSignal
): Promise<void> {
	const response = await fetch(`/api/chat/turns/${turnId}/events`, {
		headers: { Authorization: `Bearer ${token}` },
		signal
	});
	if (!response.ok || !response.body) {
		throw new ApiFailure(response.status, 'stream_failed', 'Lost the connection to the agent.');
	}
	const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
	let buffer = '';
	for (;;) {
		const { value, done } = await reader.read();
		if (done) return;
		const parsed = parseFrames(buffer + value);
		buffer = parsed.rest;
		parsed.events.forEach(onEvent);
	}
}

// Response shapes, mirroring the Pydantic models in dephx_bot/settings and dephx_bot/chat.

export type KeyStatus = { is_set: boolean; prefix: string | null };
export type ProviderOption = { id: string; label: string; min_token_limit: number };
export type CustomProvider = {
	base_url: string;
	model: string;
	context_window: number;
	max_output_tokens: number;
};
export type SettingsView = {
	provider: string;
	providers: ProviderOption[];
	keys: Record<string, KeyStatus>;
	token_limit_per_chat: number;
	custom: CustomProvider | null;
};
export type ProjectView = { name: string; root: string; needs_trust: string[] };
export type ChatState = {
	project: ProjectView | null;
	provider: string | null;
	phase: Phase;
	chat_used: number;
	chat_limit: number;
	context_tokens: number;
	context_budget: number;
	running_turn: string | null;
	pending_approvals: ApprovalRequired[];
	can_undo_compaction: boolean;
};
