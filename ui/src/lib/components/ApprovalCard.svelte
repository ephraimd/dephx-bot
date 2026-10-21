<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.js';
	import { Textarea } from '#lib/components/ui/textarea/index.js';
	import type { Decision } from '#lib/events.js';
	import { renderMarkdown } from '#lib/markdown.js';
	import type { ApprovalItem } from '#lib/transcript.svelte.js';

	let {
		item,
		onAnswer
	}: { item: ApprovalItem; onAnswer: (decision: Decision, feedback?: string) => Promise<void> } = $props();

	let pending = $state(false);
	let error = $state('');
	let askingForChanges = $state(false);
	let feedback = $state('');

	const answered: Record<Decision, string> = {
		run_once: 'You ran this command once.',
		always: 'You allowed this command for this project.',
		deny: 'You denied this command.',
		approve: 'You approved the plan.',
		request_changes: 'You asked for changes.'
	};

	async function answer(decision: Decision, text?: string) {
		pending = true;
		error = '';
		try {
			await onAnswer(decision, text);
		} catch (failure) {
			error = failure instanceof Error ? failure.message : 'Could not send your answer.';
		} finally {
			pending = false;
		}
	}
</script>

<section class="rounded-md border border-primary/30 bg-card p-3 text-sm">
	{#if item.approvalKind === 'command'}
		<h3 class="mb-2 font-medium">The agent wants to run a command</h3>
		<pre class="mb-3 overflow-x-auto rounded bg-muted p-2 font-mono text-xs">{item.text}</pre>
	{:else}
		<h3 class="mb-2 font-medium">The agent's plan needs your approval</h3>
		<div class="prose-chat mb-3 max-h-80 overflow-auto rounded bg-muted/40 p-3">{@html renderMarkdown(item.text)}</div>
	{/if}

	{#if item.decision}
		<p class="text-muted-foreground">{answered[item.decision]}</p>
	{:else if item.expired}
		<p class="text-muted-foreground">This question closed when the turn ended.</p>
	{:else if item.approvalKind === 'command'}
		<div class="flex flex-wrap items-center gap-2">
			<Button size="sm" disabled={pending} onclick={() => answer('run_once')}>Run once</Button>
			<Button size="sm" variant="outline" disabled={pending} onclick={() => answer('always')}>
				Always allow in this project
			</Button>
			<Button size="sm" variant="ghost" disabled={pending} onclick={() => answer('deny')}>Deny</Button>
		</div>
		<p class="mt-2 text-xs text-muted-foreground">
			"Always allow" runs whatever this command runs, including code the agent wrote.
		</p>
	{:else if askingForChanges}
		<Textarea bind:value={feedback} rows={3} placeholder="What should change in the plan?" />
		<div class="mt-2 flex gap-2">
			<Button size="sm" disabled={pending || !feedback.trim()} onclick={() => answer('request_changes', feedback)}>
				Send feedback
			</Button>
			<Button size="sm" variant="ghost" disabled={pending} onclick={() => (askingForChanges = false)}>Back</Button>
		</div>
	{:else}
		<div class="flex gap-2">
			<Button size="sm" disabled={pending} onclick={() => answer('approve')}>Approve</Button>
			<Button size="sm" variant="outline" disabled={pending} onclick={() => (askingForChanges = true)}>
				Request changes
			</Button>
		</div>
	{/if}

	{#if error}
		<p class="mt-2 text-xs text-destructive" role="alert">{error}</p>
	{/if}
</section>
