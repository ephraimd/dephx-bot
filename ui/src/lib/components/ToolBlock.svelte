<script lang="ts">
	import type { ToolItem } from '#lib/transcript.svelte.js';

	let { item }: { item: ToolItem } = $props();

	const stateWord = { running: 'Running', done: 'Done', failed: 'Failed' } as const;
</script>

<details class="group rounded-md border bg-card text-sm" open={item.state === 'failed'}>
	<summary class="flex cursor-pointer list-none items-center gap-2 px-3 py-2">
		<span class="font-mono text-xs">{item.name}</span>
		<span class="min-w-0 truncate text-muted-foreground">{item.summary}</span>
		<span
			class={[
				'ml-auto shrink-0 text-xs',
				item.state === 'running' && 'text-muted-foreground',
				item.state === 'failed' && 'text-destructive font-medium'
			]}
		>
			{stateWord[item.state]}
		</span>
		{#if item.state !== 'running'}
			<span class="shrink-0 text-xs tabular-nums text-muted-foreground" title="Every later request carries this output">
				+{item.addedTokens.toLocaleString('en-US')} tokens to context
			</span>
		{/if}
	</summary>
	{#if item.output}
		<pre class="max-h-80 overflow-auto border-t bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap">{item.output}</pre>
	{/if}
</details>
