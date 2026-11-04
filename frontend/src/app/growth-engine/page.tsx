'use client';
import CrmShell from '../../components/crm-shell';
import GrowthWorkspace from '../../components/growth-engine/growth-workspace';
import './growth.css';

export default function GrowthEnginePage() {
  return (
    <CrmShell>
      <GrowthWorkspace />
    </CrmShell>
  );
}
