import FaqManager from '../../../components/faq-manager';
export default async function Faq({params}:{params:Promise<{id:string}>}){const {id}=await params;return <FaqManager id={id}/>;}
