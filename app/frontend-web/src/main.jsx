import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import AuthGate from './components/AuthGate.jsx';
import DemoNotice from './components/DemoNotice.jsx';
import './styles.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <DemoNotice />
    <AuthGate>{({ user, onSignOut }) => <App user={user} onSignOut={onSignOut} />}</AuthGate>
  </React.StrictMode>
);
