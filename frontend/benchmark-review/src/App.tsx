import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Column,
  Content,
  Grid,
  Header,
  HeaderName,
  Tag,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
  Tile,
} from "@carbon/react";
import { IbmWatsonDiscovery } from "@carbon/icons-react";

import { AiReadyPanel } from "./AiReadyPanel";
import { BatchPanel } from "./BatchPanel";
import { buildInfoLabel, buildInfoTitle } from "./build-info";
import { CarbonAgentChat, type GrillExternalCommand } from "./CarbonAgentChat";
import { DiscoveryContextRail } from "./DiscoveryContextRail";
import { HistoryPanel } from "./HistoryPanel";
import { SettingsPanel } from "./SettingsPanel";
import { SingleTaskPanel } from "./SingleTaskPanel";
import { createEmptyIntent, type GrillPhase, type IntentSpec } from "./intent-spec";
import { reconcileSearchTermSelection, searchTermCandidates } from "./grill-tree";
import { getDiscoveryJob, getDiscoveryRun, type DiscoveryBatchHandoff, type DiscoveryJob, type WorkflowRecord } from "./workflow-api";

const terminal = (status: unknown) =>
  ["completed", "failed", "blocked", "cancelled"].includes(String(status || "").toLowerCase());

export default function App() {
  const [selectedTab, setSelectedTab] = useState(0);
  const [job, setJob] = useState<DiscoveryJob | null>(null);
  const [taskId, setTaskId] = useState("");
  const [batchId, setBatchId] = useState("");
  const [batchSeedInputs, setBatchSeedInputs] = useState("");
  const [batchSeedRecords, setBatchSeedRecords] = useState<WorkflowRecord[]>([]);
  const [batchSeedSource, setBatchSeedSource] = useState<{ jobId?: string; discoveryId?: string; batchIndex?: number }>({});
  const [batchSeedToken, setBatchSeedToken] = useState(0);
  const [historyKey, setHistoryKey] = useState(0);
  const [intent, setIntent] = useState<IntentSpec>(() => createEmptyIntent());
  const [selectedSearchTerms, setSelectedSearchTerms] = useState<string[]>([]);
  const [phase, setPhase] = useState<GrillPhase>("idle");
  const [externalCommand, setExternalCommand] = useState<GrillExternalCommand | null>(null);
  const [resultOpenRequest, setResultOpenRequest] = useState<{ jobId: string; token: number } | null>(null);
  const searchTermCandidatesForIntent = useMemo(
    () => searchTermCandidates(intent),
    [intent],
  );
  const searchTermCandidateKey = searchTermCandidatesForIntent.join("\u0000");

  useEffect(() => {
    setSelectedSearchTerms((current) => {
      return reconcileSearchTermSelection(current, intent);
    });
    // A scientific-theme change deliberately resets stale selections.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchTermCandidateKey]);

  const changed = () => setHistoryKey((value) => value + 1);
  const onNavigate = useCallback((tabIndex: number) => setSelectedTab(tabIndex), []);
  const onSeedBatchInputs = useCallback((handoff: Pick<DiscoveryBatchHandoff, "inputs" | "input_records" | "job_id" | "discovery_id" | "batch_index">) => {
    const next = (handoff.inputs || []).join("\n").trim();
    if (!next) return;
    setBatchSeedInputs(next);
    setBatchSeedRecords(handoff.input_records || []);
    setBatchSeedSource({
      jobId: handoff.job_id,
      discoveryId: handoff.discovery_id,
      batchIndex: handoff.batch_index,
    });
    setBatchSeedToken((token) => token + 1);
    setSelectedTab(2);
  }, []);

  return (
    <>
      <Header aria-label="PRIDE 蛋白质组学数据工作台">
        <HeaderName prefix="PRIDE">蛋白质组学数据 Agent</HeaderName>
      </Header>
      <Content className={`workbench${selectedTab === 0 ? " workbench--discovery" : ""}`}>
        <Grid fullWidth className="workbench-grid">
          <Column sm={4} md={8} lg={16} xlg={16}>
            <div className="workspace-heading">
              <div>
                <p className="eyebrow">PROTEOMICS DATA OPERATIONS</p>
                <h1>蛋白质组学数据搜集与处理 Agent</h1>
                <p>自然语言对齐科学需求，确认策略后再检索、审查并交付可追溯结果。</p>
              </div>
              <div className="workspace-heading__meta">
                <Tag type="purple" renderIcon={IbmWatsonDiscovery}>OpenAI Agents SDK</Tag>
                <span
                  className="build-stamp"
                  aria-label="构建身份"
                  title={buildInfoTitle()}
                >
                  Build {buildInfoLabel()}
                </span>
              </div>
            </div>
          </Column>

          <Column sm={4} md={8} lg={16} xlg={16} className="workbench-tabs-column">
            <Tabs
              selectedIndex={selectedTab}
              onChange={({ selectedIndex }) => setSelectedTab(selectedIndex)}
            >
              <TabList aria-label="蛋白质组学数据工作流">
                <Tab>数据发现</Tab>
                <Tab>单文件处理</Tab>
                <Tab>批量处理</Tab>
                <Tab>AI-ready 构建</Tab>
                <Tab>运行历史</Tab>
                <Tab>设置</Tab>
              </TabList>
              <TabPanels>
                <TabPanel className="discovery-tab-panel">
                  <div className="agent-layout agent-layout--grill">
                    <div className="agent-column">
                      <Tile className="agent-surface">
                        <CarbonAgentChat
                          onJob={(next) => {
                            setJob(next);
                            if (terminal(next.status)) changed();
                          }}
                          onIntentChange={setIntent}
                          onPhaseChange={setPhase}
                          externalSelectedSearchTerms={selectedSearchTerms}
                          onNavigate={onNavigate}
                          onOpenResults={(jobId) => {
                            setResultOpenRequest((prev) => ({
                              jobId,
                              token: (prev?.token || 0) + 1,
                            }));
                          }}
                          externalCommand={externalCommand}
                          onExternalCommandConsumed={() => setExternalCommand(null)}
                        />
                      </Tile>
                    </div>
                    <DiscoveryContextRail
                      spec={intent}
                      phase={phase}
                      job={job}
                      onConfirm={(queryTerms) => setExternalCommand({ type: "confirm", queryTerms })}
                      selectedSearchTerms={selectedSearchTerms}
                      onSelectedSearchTermsChange={setSelectedSearchTerms}
                      onApplyDefaults={() => setExternalCommand({ type: "defaults" })}
                      onNavigate={onNavigate}
                      onSeedBatchInputs={onSeedBatchInputs}
                      openResultRequest={resultOpenRequest}
                    />
                  </div>
                </TabPanel>
                <TabPanel>
                  <SingleTaskPanel taskId={taskId} onTaskId={setTaskId} onChanged={changed} />
                </TabPanel>
                <TabPanel>
                  <BatchPanel
                    batchId={batchId}
                    onBatchId={setBatchId}
                    onChanged={changed}
                    initialInputs={batchSeedInputs}
                    initialInputsToken={batchSeedToken}
                    initialInputRecords={batchSeedRecords}
                    initialSource={batchSeedSource}
                  />
                </TabPanel>
                <TabPanel>
                  <AiReadyPanel onChanged={changed} />
                </TabPanel>
                <TabPanel>
                  <HistoryPanel
                    refreshKey={historyKey}
                    onOpenTask={(id) => {
                      setTaskId(id);
                      setSelectedTab(1);
                    }}
                    onOpenBatch={(id) => {
                      setBatchId(id);
                      setSelectedTab(2);
                    }}
                    onOpenDiscovery={async (item) => {
                      const jobId = String(item.job_id || "");
                      const discoveryId = String(item.discovery_id || item.run_id || "");
                      const next = jobId
                        ? await getDiscoveryJob(jobId, true)
                        : {
                            job_id: discoveryId,
                            status: String(item.status || "completed"),
                            record: await getDiscoveryRun(discoveryId),
                            result_batches: [],
                            logs: [],
                          };
                      setJob(next as DiscoveryJob);
                      setSelectedTab(0);
                      setResultOpenRequest({
                        jobId: String(next.job_id || discoveryId),
                        token: Date.now(),
                      });
                    }}
                  />
                </TabPanel>
                <TabPanel>
                  <SettingsPanel />
                </TabPanel>
              </TabPanels>
            </Tabs>
          </Column>
        </Grid>
      </Content>
    </>
  );
}
