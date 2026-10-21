<script lang="ts">
	import type { Phase } from '#lib/events.js';

	let { phase }: { phase: Phase } = $props();

	const steps: { id: Phase; label: string }[] = [
		{ id: 'evaluate', label: 'Evaluate' },
		{ id: 'ideate', label: 'Ideate' },
		{ id: 'build', label: 'Build' },
		{ id: 'test', label: 'Test' },
		{ id: 'done', label: 'Done' }
	];
	const current = $derived(steps.findIndex((step) => step.id === phase));
</script>

<ol class="flex items-center gap-1 text-xs" aria-label="Agent phase">
	{#each steps as step, index (step.id)}
		<li
			class={[
				'rounded px-1.5 py-0.5',
				index === current && 'bg-primary text-primary-foreground font-medium',
				index < current && 'text-foreground',
				index > current && 'text-muted-foreground'
			]}
			aria-current={index === current ? 'step' : undefined}
		>
			{step.label}
		</li>
	{/each}
</ol>
