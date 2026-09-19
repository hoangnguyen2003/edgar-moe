import { lazy, Suspense, useEffect } from "react";
import { Layout } from "./components/Layout";
import { LoadingState } from "./components/QueryState";
import { useRouter } from "./lib/router-context";
import { OverviewPage } from "./pages/OverviewPage";

const ResearchPage = lazy(() => import("./pages/ResearchPage").then((module) => ({ default: module.ResearchPage })));
const PortfolioPage = lazy(() => import("./pages/PortfolioPage").then((module) => ({ default: module.PortfolioPage })));
const FilingsPage = lazy(() => import("./pages/FilingsPage").then((module) => ({ default: module.FilingsPage })));
const SignalsPage = lazy(() => import("./pages/SignalsPage").then((module) => ({ default: module.SignalsPage })));
const MethodologyPage = lazy(() => import("./pages/MethodologyPage").then((module) => ({ default: module.MethodologyPage })));
const ForwardPage = lazy(() => import("./pages/ForwardPage").then((module) => ({ default: module.ForwardPage })));
const GovernancePage = lazy(() => import("./pages/GovernancePage").then((module) => ({ default: module.GovernancePage })));

const routes = {
  "/": OverviewPage,
  "/research": ResearchPage,
  "/portfolio": PortfolioPage,
  "/filings": FilingsPage,
  "/signals": SignalsPage,
  "/forward": ForwardPage,
  "/governance": GovernancePage,
  "/methodology": MethodologyPage,
};

export default function App() {
  const { pathname, navigate } = useRouter();
  const Page = routes[pathname as keyof typeof routes];

  useEffect(() => {
    if (!Page) navigate("/", { replace: true });
  }, [Page, navigate]);

  return (
    <Layout>
      <Suspense fallback={<div className="page"><LoadingState /></div>}>
        {Page ? <Page /> : null}
      </Suspense>
    </Layout>
  );
}
