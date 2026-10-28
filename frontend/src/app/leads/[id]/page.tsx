import CrmRecords from '../../../components/crm-records';
export default async function Lead({params}:{params:Promise<{id:string}>}){const {id}=await params;return <CrmRecords entity="leads" id={id}/>;}
