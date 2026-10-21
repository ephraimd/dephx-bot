import { defineConfig } from 'vitest/config';
import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';

// Must match DEV_PORT in dephx_bot/config.py.
const DEV_API = 'http://127.0.0.1:8765';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// The Python package serves the built UI, so the build lands inside it.
			adapter: adapter({ pages: '../dephx_bot/static', assets: '../dephx_bot/static' }),
			csp: {
				mode: 'hash',
				directives: {
					'default-src': ['self'],
					'script-src': ['self'],
					'style-src': ['self', 'unsafe-inline'],
					// Remote images load only after a click (see markdown.ts), never on their own.
					'img-src': ['self', 'blob:', 'data:', 'https:'],
					'font-src': ['self'],
					'connect-src': ['self'],
					'object-src': ['none'],
					'base-uri': ['self'],
					'form-action': ['none']
				}
			}
		})
	],
	server: {
		// changeOrigin rewrites Host to 127.0.0.1:8765, which the backend's Host check requires.
		proxy: { '/api': { target: DEV_API, changeOrigin: true } }
	},
	test: {
		expect: { requireAssertions: true },
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'unit',
					environment: 'jsdom',
					include: ['src/**/*.{test,spec}.{js,ts}']
				}
			}
		]
	}
});
