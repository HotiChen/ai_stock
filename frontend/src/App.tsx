import React, { Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import PageSkeleton from './components/PageSkeleton';

// Lazy page imports
const Login = React.lazy(() => import('./pages/Login'));
const Dashboard = React.lazy(() => import('./pages/Dashboard'));
const TopN = React.lazy(() => import('./pages/predict/TopN'));
const DeepAnalysis = React.lazy(() => import('./pages/predict/DeepAnalysis'));
const Reasoning = React.lazy(() => import('./pages/predict/Reasoning'));
const Cockpit = React.lazy(() => import('./pages/daytrade/Cockpit'));
const Chart = React.lazy(() => import('./pages/daytrade/Chart'));
const Order = React.lazy(() => import('./pages/daytrade/Order'));
const Portfolio = React.lazy(() => import('./pages/Portfolio'));
const Journal = React.lazy(() => import('./pages/Journal'));
const Simulate = React.lazy(() => import('./pages/Simulate'));
const Report = React.lazy(() => import('./pages/Report'));
const Scanner = React.lazy(() => import('./pages/Scanner'));
const Settings = React.lazy(() => import('./pages/Settings'));
const MobilePredict = React.lazy(() => import('./pages/mobile/MobilePredict'));
const MobileDaytrade = React.lazy(() => import('./pages/mobile/MobileDaytrade'));

// Auth guard
function isAuthenticated(): boolean {
  return !!localStorage.getItem('access_token');
}

interface ProtectedRouteProps {
  children: React.ReactNode;
}

function ProtectedRoute({ children }: ProtectedRouteProps) {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public */}
        <Route
          path="/login"
          element={
            <Suspense fallback={<PageSkeleton />}>
              <Login />
            </Suspense>
          }
        />

        {/* Protected routes */}
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Dashboard />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/predict"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <TopN />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/predict/:code"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <DeepAnalysis />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/predict/:code/reasoning"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Reasoning />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/daytrade"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Cockpit />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/daytrade/:code/chart"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Chart />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/daytrade/order"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Order />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/portfolio"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Portfolio />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/journal"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Journal />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/simulate"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Simulate />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/report"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Report />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/scanner"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Scanner />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/settings"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <Settings />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/m/predict"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <MobilePredict />
              </Suspense>
            </ProtectedRoute>
          }
        />

        <Route
          path="/m/daytrade"
          element={
            <ProtectedRoute>
              <Suspense fallback={<PageSkeleton />}>
                <MobileDaytrade />
              </Suspense>
            </ProtectedRoute>
          }
        />

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
