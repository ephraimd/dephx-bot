<script lang="ts">
	import { api, ApiFailure, type ProjectView } from '#lib/api.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';

	let { onOpened }: { onOpened: (project: ProjectView) => void } = $props();

	let path = $state('');
	let error = $state('');
	let opening = $state(false);
	let canBrowse = $state(true);
	let untrusted = $state<ProjectView | null>(null);

	async function browse() {
		error = '';
		try {
			const chosen = await api<{ path: string | null }>('POST', '/api/project/browse');
			if (chosen.path) path = chosen.path;
		} catch (failure) {
			if (failure instanceof ApiFailure && failure.code === 'no_window') canBrowse = false;
			else error = failure instanceof Error ? failure.message : 'Could not open the folder picker.';
		}
	}

	async function open(event: SubmitEvent) {
		event.preventDefault();
		opening = true;
		error = '';
		try {
			const project = await api<ProjectView>('POST', '/api/project/open', { path });
			if (project.needs_trust.length) untrusted = project;
			else onOpened(project);
		} catch (failure) {
			error = failure instanceof Error ? failure.message : 'Could not open that folder.';
		} finally {
			opening = false;
		}
	}

	async function trust() {
		onOpened(await api<ProjectView>('POST', '/api/project/trust'));
	}
</script>

<form class="grid gap-3" onsubmit={open}>
	<label class="text-sm font-medium" for="project-path">Project folder</label>
	<div class="flex gap-2">
		<Input id="project-path" class="flex-1" placeholder="Full path to the folder" bind:value={path} required />
		{#if canBrowse}
			<Button type="button" variant="outline" onclick={browse}>Browse...</Button>
		{/if}
	</div>
	<p class="text-xs text-muted-foreground">The agent can read and change files only inside this folder.</p>
	{#if error}
		<p class="text-sm text-destructive" role="alert">{error}</p>
	{/if}
	<div>
		<Button type="submit" disabled={opening || !path.trim()}>{opening ? 'Opening...' : 'Open folder'}</Button>
	</div>
</form>

<Dialog.Root open={untrusted !== null} onOpenChange={(isOpen) => !isOpen && (untrusted = null)}>
	<Dialog.Content>
		<Dialog.Header>
			<Dialog.Title>Trust this folder's commands?</Dialog.Title>
			<Dialog.Description>
				This folder already allows these commands to run without asking. Only trust them if you, not someone
				else, set them up.
			</Dialog.Description>
		</Dialog.Header>
		<ul class="grid gap-1">
			{#each untrusted?.needs_trust ?? [] as command (command)}
				<li class="rounded bg-muted px-2 py-1 font-mono text-xs">{command}</li>
			{/each}
		</ul>
		<Dialog.Footer>
			<Button variant="outline" onclick={() => untrusted && onOpened(untrusted)}>Open without trusting</Button>
			<Button onclick={trust}>Trust and open</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
