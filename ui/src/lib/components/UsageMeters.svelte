<script lang="ts">
	let {
		used,
		limit,
		context,
		budget,
		onEditLimit
	}: { used: number; limit: number; context: number; budget: number; onEditLimit: () => void } = $props();

	const tokens = (n: number) => n.toLocaleString('en-US');
	const thousands = (n: number) => `${Math.round(n / 1000)}K`;
	const nearLimit = $derived(limit > 0 && used / limit > 0.8);
	const contextFull = $derived(budget > 0 && context / budget > 0.8);
</script>

<div class="flex items-center gap-3 text-xs tabular-nums text-muted-foreground">
	<button
		type="button"
		class={['rounded px-1 hover:bg-muted', nearLimit && 'text-destructive']}
		title="Tokens this chat has used, against its limit. Click to change the limit."
		onclick={onEditLimit}
	>
		{tokens(used)} / {tokens(limit)} tokens
	</button>
	<span class={[contextFull && 'text-destructive']} title="How full the next request is, against what the provider accepts">
		Context {thousands(context)} / {thousands(budget)}
	</span>
</div>
