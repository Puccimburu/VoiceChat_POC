/**
 * Shared widget loader — reads data-* attributes from the <script> tag
 * and mounts any React component into a target container.
 *
 * Usage (in each entry point):
 *   import { mountWidget } from './utils/widget-loader';
 *   import MyComponent from './MyComponent';
 *   mountWidget(MyComponent);
 */
import React from 'react';
import { createRoot } from 'react-dom/client';

export function mountWidget(Component, scriptSelector) {
  const script =
    document.currentScript ||
    document.querySelector(scriptSelector || 'script[data-api-key]');

  const get = (attr, fallback = '') =>
    (script && script.getAttribute(attr)) || fallback;

  const apiKey    = get('data-api-key');
  const agentName = get('data-agent-name', 'AI Assistant');
  const apiUrl    = get('data-api-url');
  const wsUrl     = get('data-ws-url');
  const voice     = get('data-voice');
  const mode      = get('data-mode', 'general');

  // Use data-target if provided, otherwise create a floating container
  const targetId = get('data-target');
  let container = targetId ? document.getElementById(targetId) : null;
  if (!container) {
    container = document.createElement('div');
    container.id = targetId || `__widget-root-${Date.now()}__`;
    document.body.appendChild(container);
  }

  createRoot(container).render(
    React.createElement(Component, { apiKey, agentName, apiUrl, wsUrl, voice, mode })
  );
}
