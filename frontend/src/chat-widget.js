/**
 * chat-widget.js — floating chat + voice widget entry point.
 *
 *   <script
 *     src="https://yourplatform.com/chat-widget.js"
 *     data-api-key="va_..."
 *     data-agent-name="My Assistant"
 *     data-api-url="http://localhost:5001"
 *     data-ws-url="ws://localhost:8080/ws"
 *     data-mode="agent"
 *   ></script>
 */
import { mountWidget } from './utils/widget-loader';
import ChatWidget from './ChatWidget';

(function () {
  mountWidget(ChatWidget, 'script[data-api-key][src*="chat-widget"]');
})();
