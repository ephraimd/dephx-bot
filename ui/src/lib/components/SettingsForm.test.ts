import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';
import SettingsForm from './SettingsForm.svelte';
import type { SettingsView } from '#lib/api.js';

const settings: SettingsView = {
	provider: 'praxiom',
	providers: [
		{ id: 'praxiom', label: 'Praxiom', min_token_limit: 65_536 },
		{ id: 'deepseek', label: 'DeepSeek', min_token_limit: 128_192 },
		{ id: 'custom', label: 'Custom (any OpenAI-compatible API)', min_token_limit: 0 }
	],
	keys: {},
	token_limit_per_chat: 300_000,
	custom: { base_url: 'https://api.example.com/v1', model: 'm', context_window: 131_072, max_output_tokens: 4_000 }
};

let form: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (form) unmount(form);
	form = null;
	document.body.innerHTML = '';
});

function show(view: SettingsView) {
	form = mount(SettingsForm, { target: document.body, props: { settings: view, onSaved: () => {} } });
	flushSync();
}

function input(id: string): HTMLInputElement {
	return document.getElementById(id) as HTMLInputElement;
}

describe('SettingsForm', () => {
	// The browser refuses to submit a number that is not min + n * step, which blocked the default limit.
	it('accepts the default token limit for every provider', () => {
		for (const provider of ['praxiom', 'deepseek']) {
			show({ ...settings, provider });
			expect(input('token-limit').validity.valid, provider).toBe(true);
			unmount(form!);
			form = null;
		}
	});

	it('accepts real context windows and reply sizes for a custom provider', () => {
		show({ ...settings, provider: 'custom' });
		expect(input('custom-window').validity.valid).toBe(true);
		expect(input('custom-output').validity.valid).toBe(true);
	});

	it('still refuses a limit below the provider minimum', () => {
		show({ ...settings, token_limit_per_chat: 1_000 });
		expect(input('token-limit').validity.rangeUnderflow).toBe(true);
	});
});
