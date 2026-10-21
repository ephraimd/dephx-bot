<script lang="ts">
	import { untrack } from 'svelte';
	import { api, type SettingsView } from '#lib/api.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import * as Select from '#lib/components/ui/select/index.js';

	let { settings, onSaved }: { settings: SettingsView; onSaved: (view: SettingsView) => void } = $props();

	// Editable copies; the form only changes the server's settings on Save.
	let provider = $state(untrack(() => settings.provider));
	let apiKey = $state('');
	let limit = $state(untrack(() => settings.token_limit_per_chat));
	let saving = $state(false);
	let result: { ok: boolean; text: string } | null = $state(null);

	const option = $derived(settings.providers.find((p) => p.id === provider));
	const keyStatus = $derived(settings.keys[provider]);

	async function save(event: SubmitEvent) {
		event.preventDefault();
		saving = true;
		result = null;
		try {
			const body: Record<string, unknown> = { provider, token_limit_per_chat: limit };
			if (apiKey.trim()) body.api_key = apiKey;
			const view = await api<SettingsView>('PUT', '/api/settings', body);
			apiKey = '';
			result = { ok: true, text: 'Saved' };
			onSaved(view);
		} catch (failure) {
			result = { ok: false, text: failure instanceof Error ? failure.message : 'Could not save.' };
		} finally {
			saving = false;
		}
	}
</script>

<form class="grid gap-5" onsubmit={save}>
	<div class="grid gap-1.5">
		<label class="text-sm font-medium" for="provider">Model provider</label>
		<Select.Root type="single" bind:value={provider}>
			<Select.Trigger id="provider" class="w-full">{option?.label ?? 'Choose a provider'}</Select.Trigger>
			<Select.Content>
				{#each settings.providers as choice (choice.id)}
					<Select.Item value={choice.id} label={choice.label}>{choice.label}</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
	</div>

	<div class="grid gap-1.5">
		<label class="text-sm font-medium" for="api-key">{option?.label} API key</label>
		<Input id="api-key" type="password" autocomplete="off" placeholder="Paste your API key" bind:value={apiKey} />
		<p class="text-xs text-muted-foreground">
			{#if keyStatus?.is_set}Key set, starts with {keyStatus.prefix}. Paste a new one to replace it.{:else}No key set yet.{/if}
			Stored only on this computer.
		</p>
	</div>

	<div class="grid gap-1.5">
		<label class="text-sm font-medium" for="token-limit">Token limit per chat</label>
		<Input id="token-limit" type="number" min={option?.min_token_limit} step="1000" bind:value={limit} />
		<p class="text-xs text-muted-foreground">
			Each chat stops before using more than this. Minimum {option?.min_token_limit.toLocaleString('en-US')} for
			{option?.label}, the size of one full request.
		</p>
	</div>

	<div class="flex items-center gap-3">
		<Button type="submit" disabled={saving}>{saving ? 'Saving...' : 'Save'}</Button>
		{#if result}
			<p class={['text-sm', result.ok ? 'text-muted-foreground' : 'text-destructive']} role="status">{result.text}</p>
		{/if}
	</div>
</form>
