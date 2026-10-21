import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseFrames } from './api';

describe('parseFrames', () => {
	it('splits complete server-sent events and keeps the unfinished rest', () => {
		const buffer =
			'data: {"type":"token","step":1,"text":"Hi"}\n\n' + 'data: {"type":"usage"';
		const { events, rest } = parseFrames(buffer);

		expect(events).toEqual([{ type: 'token', step: 1, text: 'Hi' }]);
		expect(rest).toBe('data: {"type":"usage"');
	});

	it('ignores lines that are not data lines', () => {
		const { events } = parseFrames(': keep-alive\n\ndata: {"type":"error","code":"x","message":"y"}\n\n');
		expect(events).toEqual([{ type: 'error', code: 'x', message: 'y' }]);
	});
});

describe('launch token', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		sessionStorage.clear();
	});

	// A reload starts the page with fresh modules, so each "page load" imports api.ts anew.
	async function loadPage(url: string) {
		vi.resetModules();
		history.replaceState(null, '', url);
		const module = await import('./api');
		module.initToken();
		return module;
	}

	async function authorizationSent(module: typeof import('./api')): Promise<string> {
		const fetchMock = vi.fn(async (_path: string, _init: RequestInit) => new Response('{"ok":true}'));
		vi.stubGlobal('fetch', fetchMock);
		await module.api('GET', '/api/health');
		const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
		return headers.Authorization;
	}

	it('reads the token from the fragment and removes it from the address', async () => {
		const page = await loadPage('/#token=abc123');
		expect(location.hash).toBe('');
		expect(await authorizationSent(page)).toBe('Bearer abc123');
	});

	it('survives a reload, when the fragment is already gone', async () => {
		await loadPage('/#token=keep-me');
		const reloaded = await loadPage('/');
		expect(await authorizationSent(reloaded)).toBe('Bearer keep-me');
	});
});
