import CrmRecords from '../../../components/crm-records';
export default async function Property({params}:{params:Promise<{id:string}>}){const {id}=await params;return <CrmRecords entity="properties" id={id}/>;}
