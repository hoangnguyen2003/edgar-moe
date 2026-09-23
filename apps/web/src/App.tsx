import { lazy, Suspense, useEffect } from "react";
import { PageErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import { LoadingState } from "./components/QueryState";
import { canonicalPath } from "./lib/navigation";
import { useRouter } from "./lib/router-context";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OverviewPage } from "./pages/OverviewPage";

const ResearchPage = lazy(() => import("./pages/ResearchPage").then((module) => ({ default: module.ResearchPage })));
const PortfolioPage = lazy(() => import("./pages/PortfolioPage").then((module) => ({ default: module.PortfolioPage })));
const FilingsPage = lazy(() => import("./pages/FilingsPage").then((module) => ({ default: module.FilingsPage })));
const SignalsPage = lazy(() => import("./pages/SignalsPage").then((module) => ({ default: module.SignalsPage })));
const MethodologyPage = lazy(() => import("./pages/MethodologyPage").then((module) => ({ default: module.MethodologyPage })));
const ForwardPage = lazy(() => import("./pages/ForwardPage").then((module) => ({ default: module.ForwardPage })));
const GovernancePage = lazy(() => import("./pages/GovernancePage").then((module) => ({ default: module.GovernancePage })));
const ArchitecturePage = lazy(() => import("./pages/ArchitecturePage").then((module) => ({ default: module.ArchitecturePage })));

const routes = {
  "/": OverviewPage,
  "/research": ResearchPage,
  "/portfolio": PortfolioPage,
  "/filings": FilingsPage,
  "/signals": SignalsPage,
  "/forward": ForwardPage,
  "/governance": GovernancePage,
  "/methodology": MethodologyPage,
  "/architecture": ArchitecturePage,
};

export default function App() {
  const { pathname, navigate } = useRouter();
  const Page = Object.hasOwn(routes, pathname) ? routes[pathname as keyof typeof routes] : undefined;
  // A page's visible name or a miscased path leads to the page it means.
  const canonical = Page ? null : canonicalPath(pathname);

  useEffect(() => {
    if (canonical) navigate(canonical, { replace: true });
  }, [canonical, navigate]);

  return (
    <Layout>
      <PageErrorBoundary resetKey={pathname}>
        <Suspense fallback={<div className="page"><LoadingState label="Loading the page" skeleton={["rows"]} /></div>}>
          {Page ? <Page /> : canonical ? null : <NotFoundPage pathname={pathname} />}
        </Suspense>
      </PageErrorBoundary>
    </Layout>
  );
}
