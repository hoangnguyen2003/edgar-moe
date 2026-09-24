import { lazy, Suspense, useEffect } from "react";
import { PageErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import { LoadingState } from "./components/QueryState";
import { canonicalPath } from "./lib/navigation";
import { useRouter } from "./lib/router-context";
import { pageLoaders } from "./lib/routes";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OverviewPage } from "./pages/OverviewPage";

const ResearchPage = lazy(pageLoaders["/research"]);
const PortfolioPage = lazy(pageLoaders["/portfolio"]);
const FilingsPage = lazy(pageLoaders["/filings"]);
const SignalsPage = lazy(pageLoaders["/signals"]);
const MethodologyPage = lazy(pageLoaders["/methodology"]);
const ForwardPage = lazy(pageLoaders["/forward"]);
const GovernancePage = lazy(pageLoaders["/governance"]);
const ArchitecturePage = lazy(pageLoaders["/architecture"]);

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
