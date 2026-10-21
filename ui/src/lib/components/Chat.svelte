<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import { api, streamTurn, type ChatState, type ProjectView } from '#lib/api.js';
	import ApprovalCard from '#lib/components/ApprovalCard.svelte';
	import Message from '#lib/components/Message.svelte';
	import PhaseSteps from '#lib/components/PhaseSteps.svelte';
	import ToolBlock from '#lib/components/ToolBlock.svelte';
	import UsageMeters from '#lib/components/UsageMeters.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Textarea } from '#lib/components/ui/textarea/index.js';
	import type { Decision } from '#lib/events.js';
	import { Transcript, type NoticeItem } from '#lib/transcript.svelte.js';

	let { project, onOpenSettings }: { project: ProjectView; onOpenSettings: () => void } = $props();

	const EXAMPLE = 'Add a /health endpoint and a test for it';

	const transcript = new Transcript();
	let draft = $state('');
	let sendError = $state('');
	let stream: AbortController | null = null;
	let scroller = $state<HTMLDivElement>();
	let stickToBottom = true;
	let stopping = $state(false);
	let limitDialogOpen = $state(false);
	let newLimit = $state(0);

	onMount(async () => {
		const state = await api<ChatState>('GET', '/api/chat/state');
		transcript.sync(state);
		// A reload during a turn: replay it from the start and keep following it.
		if (state.running_turn) {
			transcript.startTurn(state.running_turn, null);
			void follow(state.running_turn);
		}
	});

	onDestroy(() => stream?.abort());

	$effect(() => {
		const last = transcript.items.at(-1);
		void (last && 'text' in last ? last.text : last);
		void (last && 'output' in last ? last.output : '');
		if (stickToBottom) void tick().then(() => scroller?.scrollTo({ top: scroller.scrollHeight }));
	});

	function onScroll() {
		if (!scroller) return;
		stickToBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
	}

	async function follow(turnId: string) {
		stream = new AbortController();
		try {
			await streamTurn(turnId, (event) => transcript.apply(event), stream.signal);
		} catch {
			if (!stream.signal.aborted) {
				transcript.apply({
					type: 'error',
					code: 'stream_lost',
					message: 'Lost the connection to the agent. Reload the window to reconnect.'
				});
				transcript.running = false;
			}
		}
	}

	async function send(text: string) {
		if (!text.trim() || transcript.running) return;
		sendError = '';
		try {
			const { turn_id } = await api<{ turn_id: string }>('POST', '/api/chat/messages', { text });
			draft = '';
			stickToBottom = true;
			stopping = false;
			transcript.startTurn(turn_id, text);
			void follow(turn_id);
		} catch (failure) {
			sendError = failure instanceof Error ? failure.message : 'Could not send the message.';
		}
	}

	function onComposerKey(event: KeyboardEvent) {
		if (event.key === 'Enter' && !event.shiftKey) {
			event.preventDefault();
			void send(draft);
		}
	}

	async function answer(approvalId: string, decision: Decision, feedback?: string) {
		await api('POST', `/api/chat/approvals/${approvalId}`, { decision, feedback });
	}

	async function newChat() {
		await api('POST', '/api/chat/new');
		transcript.clear();
		transcript.sync(await api<ChatState>('GET', '/api/chat/state'));
	}

	function editLimit() {
		newLimit = Math.max(transcript.chatLimit * 2, transcript.chatUsed * 2);
		limitDialogOpen = true;
	}

	let limitError = $state('');
	async function saveLimit(event: SubmitEvent) {
		event.preventDefault();
		limitError = '';
		try {
			transcript.sync(await api<ChatState>('PUT', '/api/chat/token-limit', { limit: newLimit }));
			limitDialogOpen = false;
		} catch (failure) {
			limitError = failure instanceof Error ? failure.message : 'Could not change the limit.';
		}
	}

	const noticeTone: Record<NoticeItem['status'], string> = {
		done_verified: 'border-emerald-600/30 bg-emerald-600/5',
		done_unverified: 'border-amber-500/40 bg-amber-500/5',
		waiting_for_user: 'border-border bg-muted/40',
		incomplete: 'border-amber-500/40 bg-amber-500/5',
		stopped: 'border-border bg-muted/40',
		step_limit: 'border-amber-500/40 bg-amber-500/5',
		token_limit: 'border-amber-500/40 bg-amber-500/5',
		failed: 'border-destructive/40 bg-destructive/5',
		error: 'border-destructive/40 bg-destructive/5'
	};
</script>

<div class="flex h-dvh flex-col">
	<header class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2">
		<h1 class="truncate text-sm font-semibold" title={project.root}>{project.name}</h1>
		<PhaseSteps phase={transcript.phase} />
		<div class="ml-auto flex items-center gap-2">
			<UsageMeters
				used={transcript.chatUsed}
				limit={transcript.chatLimit}
				context={transcript.contextTokens}
				budget={transcript.contextBudget}
				onEditLimit={editLimit}
			/>
			<Button size="sm" variant="ghost" disabled={transcript.running} onclick={newChat}>New chat</Button>
			<Button size="sm" variant="ghost" onclick={onOpenSettings}>Settings</Button>
		</div>
	</header>

	<div bind:this={scroller} onscroll={onScroll} class="flex-1 overflow-y-auto">
		<div class="mx-auto grid max-w-3xl grid-cols-[minmax(0,1fr)] gap-3 px-4 py-6">
			{#if transcript.items.length === 0}
				<div class="py-16 text-center text-sm text-muted-foreground">
					<p>
						Ask for a change in <span class="font-medium text-foreground">{project.name}</span>. The agent will read the
						code, propose a plan for your approval, build it and test it.
					</p>
					<Button class="mt-4" variant="outline" size="sm" onclick={() => (draft = EXAMPLE)}>{EXAMPLE}</Button>
				</div>
			{/if}

			{#each transcript.items as item (item.key)}
				{#if item.kind === 'user'}
					<p class="ml-auto max-w-[85%] rounded-lg bg-muted px-3 py-2 text-sm whitespace-pre-wrap">{item.text}</p>
				{:else if item.kind === 'assistant'}
					<div><Message {item} /></div>
				{:else if item.kind === 'tool' && !(item.name === 'advance_phase' && item.state === 'done')}
					<ToolBlock {item} />
				{:else if item.kind === 'approval'}
					<ApprovalCard {item} onAnswer={(decision, feedback) => answer(item.approvalId, decision, feedback)} />
				{:else if item.kind === 'phase'}
					<div class="flex items-center gap-3 pt-2 text-xs text-muted-foreground">
						<span class="shrink-0 font-medium tracking-wide text-foreground uppercase">{item.phase}</span>
						<span class="line-clamp-2">{item.summary}</span>
					</div>
				{:else if item.kind === 'notice'}
					<div class={['rounded-md border px-3 py-2 text-sm', noticeTone[item.status]]} role="status">
						<p>{item.text}</p>
						{#if item.status === 'token_limit'}
							<Button class="mt-2" size="sm" variant="outline" onclick={editLimit}>Raise limit</Button>
						{:else if item.code === 'context_full'}
							<Button class="mt-2" size="sm" variant="outline" onclick={newChat}>New chat</Button>
						{/if}
					</div>
				{/if}
			{/each}
		</div>
	</div>

	<footer class="border-t px-4 py-3">
		<div class="mx-auto flex max-w-3xl items-end gap-2">
			<Textarea
				class="max-h-48 min-h-11 flex-1 resize-none"
				rows={1}
				placeholder="Ask for a change. Enter sends, Shift+Enter adds a line."
				bind:value={draft}
				onkeydown={onComposerKey}
			/>
			{#if transcript.running}
				<Button
					variant="outline"
					disabled={stopping}
					onclick={() => {
						stopping = true;
						void api('POST', '/api/chat/stop');
					}}
					title="Stops the agent. Tokens already generated are still billed."
				>
					{stopping ? 'Stopping...' : 'Stop'}
				</Button>
			{:else}
				<Button disabled={!draft.trim()} onclick={() => send(draft)}>Send</Button>
			{/if}
		</div>
		{#if sendError}
			<p class="mx-auto mt-2 max-w-3xl text-sm text-destructive" role="alert">{sendError}</p>
		{/if}
	</footer>
</div>

<Dialog.Root bind:open={limitDialogOpen}>
	<Dialog.Content>
		<Dialog.Header>
			<Dialog.Title>Token limit for this chat</Dialog.Title>
			<Dialog.Description>
				This chat has used {transcript.chatUsed.toLocaleString('en-US')} tokens. It stops before going over the limit.
			</Dialog.Description>
		</Dialog.Header>
		<form class="grid gap-3" onsubmit={saveLimit}>
			<Input type="number" step="1000" bind:value={newLimit} aria-label="Token limit" />
			{#if limitError}
				<p class="text-sm text-destructive" role="alert">{limitError}</p>
			{/if}
			<Dialog.Footer>
				<Button type="submit">Save limit</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
