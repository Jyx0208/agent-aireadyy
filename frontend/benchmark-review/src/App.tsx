import { useCallback, useState } from "react";
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
import { CarbonAgentChat } from "./CarbonAgentChat";
import { DiscoveryContextRail } from "./DiscoveryContextRail";
import { HistoryPanel } from "./HistoryPanel";
import { SettingsPanel } from "./SettingsPanel";
import { SingleTaskPanel } from "./SingleTaskPanel";
import { createEmptyIntent, type GrillPhase, type IntentSpec } from "./intent-spec";
import type { DiscoveryJob } from "./workflow-api";

const terminal = (status: unknown) =>
  ["completed", "failed", "blocked", "cancelled"].includes(String(status || "").toLowerCase());

export default function App() {
  const [selectedTab, setSelectedTab] = useState(0);
  const [job, setJob] = useState<DiscoveryJob | null>(null);
  const [taskId, setTaskId] = useState("");
  const [batchId, setBatchId] = useState("");
  const [historyKey, setHistoryKey] = useState(0);
  const [intent, setIntent] = useState<IntentSpec>(() => createEmptyIntent());
  const [phase, setPhase] = useState<GrillPhase>("idle");
  const [externalCommand, setExternalCommand] = useState<"confirm" | "defaults" | null>(null);

  const changed = () => setHistoryKey((value) => value + 1);
  const onNavigate = useCallback((tabIndex: number) => setSelectedTab(tabIndex), []);

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
                          onNavigate={onNavigate}
                          externalCommand={externalCommand}
                          onExternalCommandConsumed={() => setExternalCommand(null)}
                        />
                      </Tile>
                    </div>
                    <DiscoveryContextRail
                      spec={intent}
                      phase={phase}
                      job={job}
                      onConfirm={() => setExternalCommand("confirm")}
                      onApplyDefaults={() => setExternalCommand("defaults")}
                      onNavigate={onNavigate}
                    />
                  </div>
                </TabPanel>
                <TabPanel>
                  <SingleTaskPanel taskId={taskId} onTaskId={setTaskId} onChanged={changed} />
                </TabPanel>
                <TabPanel>
                  <BatchPanel batchId={batchId} onBatchId={setBatchId} onChanged={changed} />
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
