<script lang="ts">
	import { onMount } from 'svelte';
	import { api, initToken } from '#lib/api.js';

	let status = $state('Connecting...');

	onMount(async () => {
		initToken();
		try {
			await api<{ ok: boolean }>('GET', '/api/health');
			status = 'Connected to Praxium.';
		} catch (error) {
			status = error instanceof Error ? error.message : 'Could not reach Praxium.';
		}
	});
</script>

<main class="p-8 text-sm">{status}</main>
