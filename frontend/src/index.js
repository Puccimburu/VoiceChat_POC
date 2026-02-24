import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import AppStreaming from './AppStreaming';
import AdminPortal from './AdminPortal';

// Simple page switcher — no router needed
// /admin  → Admin Portal
// /       → Voice Agent UI
const isAdmin = window.location.pathname.startsWith('/admin');

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    {isAdmin ? <AdminPortal /> : <AppStreaming />}
  </React.StrictMode>
);
