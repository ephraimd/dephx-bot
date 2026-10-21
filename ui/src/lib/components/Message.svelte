<script lang="ts">
	import { api, imageUrl } from '#lib/api.js';
	import { renderMarkdown } from '#lib/markdown.js';
	import type { AssistantItem } from '#lib/transcript.svelte.js';

	let { item }: { item: AssistantItem } = $props();

	let container = $state<HTMLDivElement>();
	const html = $derived(renderMarkdown(item.text));
	// The HTML is replaced on every streamed token, so remember what was already loaded.
	const projectImages = new Map<string, string>();
	const clickedRemoteImages = new Set<string>();

	$effect(() => {
		void html;
		if (!container) return;
		for (const img of container.querySelectorAll<HTMLImageElement>('img[data-project-src]')) {
			void showProjectImage(img);
		}
		for (const button of container.querySelectorAll<HTMLElement>('[data-remote-src]')) {
			const url = button.dataset.remoteSrc ?? '';
			if (clickedRemoteImages.has(url)) button.replaceWith(remoteImage(url));
		}
	});

	$effect(() => {
		const element = container;
		if (!element) return;
		element.addEventListener('click', onClick);
		return () => element.removeEventListener('click', onClick);
	});

	$effect(() => () => projectImages.forEach((url) => URL.revokeObjectURL(url)));

	async function showProjectImage(img: HTMLImageElement) {
		const path = img.dataset.projectSrc ?? '';
		try {
			if (!projectImages.has(path)) projectImages.set(path, await imageUrl(path));
			img.src = projectImages.get(path) ?? '';
		} catch {
			img.replaceWith(document.createTextNode(`[image not found: ${path}]`));
		}
	}

	function remoteImage(url: string): HTMLImageElement {
		const img = document.createElement('img');
		img.src = url;
		img.alt = '';
		return img;
	}

	function onClick(event: MouseEvent) {
		const target = event.target as HTMLElement;
		const link = target.closest<HTMLElement>('[data-external]');
		if (link) {
			event.preventDefault();
			void api('POST', '/api/open-link', { url: link.dataset.external });
			return;
		}
		const button = target.closest<HTMLElement>('[data-remote-src]');
		if (button) {
			const url = button.dataset.remoteSrc ?? '';
			clickedRemoteImages.add(url);
			button.replaceWith(remoteImage(url));
		}
	}

	const usageLine = $derived.by(() => {
		const usage = item.usage;
		if (!usage) return '';
		const parts = [`in ${usage.input_tokens.toLocaleString('en-US')}`, `out ${usage.output_tokens.toLocaleString('en-US')}`];
		if (usage.cached_tokens) parts.push(`cached ${usage.cached_tokens.toLocaleString('en-US')}`);
		if (usage.cost !== null) parts.push(`≈ ${usage.cost.toFixed(4)} ${usage.currency ?? ''}`.trim());
		return parts.join(' · ') + (usage.estimated ? ' (estimated)' : '');
	});
</script>

{#if item.text}
	<div bind:this={container} class="prose-chat">{@html html}</div>
{/if}
{#if usageLine}
	<p class="mt-1 text-[11px] tabular-nums text-muted-foreground" title="Tokens for the model call that produced this step">
		{usageLine}
	</p>
{/if}
