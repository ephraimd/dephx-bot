<script lang="ts">
	import { onMount } from 'svelte';
	import { api, initToken, type ChatState, type ProjectView, type SettingsView } from '#lib/api.js';
	import Chat from '#lib/components/Chat.svelte';
	import OpenProject from '#lib/components/OpenProject.svelte';
	import SettingsForm from '#lib/components/SettingsForm.svelte';
	import * as Dialog from '#lib/components/ui/dialog/index.js';

	let settings = $state<SettingsView | null>(null);
	let project = $state<ProjectView | null>(null);
	let loadError = $state('');
	let settingsOpen = $state(false);

	const hasKey = $derived(settings ? settings.keys[settings.provider]?.is_set : false);

	onMount(async () => {
		initToken();
		try {
			settings = await api<SettingsView>('GET', '/api/settings');
			project = (await api<ChatState>('GET', '/api/chat/state')).project;
		} catch (failure) {
			loadError = failure instanceof Error ? failure.message : 'Could not reach Praxium.';
		}
	});
</script>

{#if loadError}
	<main class="grid h-dvh place-items-center p-6 text-sm text-destructive" role="alert">{loadError}</main>
{:else if !settings}
	<main class="grid h-dvh place-items-center p-6 text-sm text-muted-foreground">Starting Praxium...</main>
{:else if !hasKey}
	<main class="mx-auto grid max-w-md gap-6 px-6 py-16">
		<div>
			<h1 class="text-lg font-semibold">Connect a model provider</h1>
			<p class="mt-1 text-sm text-muted-foreground">Praxium needs an API key to talk to the model.</p>
		</div>
		<SettingsForm {settings} onSaved={(view) => (settings = view)} />
	</main>
{:else if !project}
	<main class="mx-auto grid max-w-lg gap-6 px-6 py-16">
		<div>
			<h1 class="text-lg font-semibold">Open a project folder</h1>
			<p class="mt-1 text-sm text-muted-foreground">Pick the folder the agent will work in.</p>
		</div>
		<OpenProject onOpened={(opened) => (project = opened)} />
	</main>
{:else}
	<Chat {project} onOpenSettings={() => (settingsOpen = true)} />
	<Dialog.Root bind:open={settingsOpen}>
		<Dialog.Content>
			<Dialog.Header>
				<Dialog.Title>Settings</Dialog.Title>
				<Dialog.Description>Changes to the provider or key apply when you start a new chat.</Dialog.Description>
			</Dialog.Header>
			<SettingsForm {settings} onSaved={(view) => (settings = view)} />
		</Dialog.Content>
	</Dialog.Root>
{/if}
