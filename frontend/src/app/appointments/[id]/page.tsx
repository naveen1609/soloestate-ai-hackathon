import CrmRecords from '../../../components/crm-records';
export default async function Appointment({params}:{params:Promise<{id:string}>}){const {id}=await params;return <CrmRecords entity="appointments" id={id}/>;}
