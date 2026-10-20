// 保持Deno.Command语义，受限Windows内用自有临时文件传输标准流。
const NativeCommand=Deno.Command;
const python=Deno.env.get('KP_QUARTO_PIPE_PYTHON');
const bridge=Deno.env.get('KP_QUARTO_PIPE_BRIDGE');
const temp=Deno.env.get('KP_QUARTO_PIPE_TEMP');
if(!python||!bridge||!temp)throw Error('QUARTO_PIPE_CONFIGURATION_MISSING');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function prepare(command,options){
 const folder=temp+'/'+crypto.randomUUID();Deno.mkdirSync(folder);
 const request={command:String(command),...options,ownerPid:Deno.pid};
 for(const name of ['stdin','stdout','stderr']){
  request[name]=request[name]??'inherit';
  if(!['piped','inherit','null'].includes(request[name]))throw Error('UNSUPPORTED_QUARTO_STREAM_MODE');
  if(request[name]==='piped')Deno.writeFileSync(folder+'/'+name+'.bin',new Uint8Array());
 }
 Deno.writeTextFileSync(folder+'/request.json',JSON.stringify(request));
 return {folder,request};
}
function errorFrom(folder){
 try {const row=JSON.parse(Deno.readTextFileSync(folder+'/error.json'));return Error(row.type+': '+row.message);}
 catch(e){if(e instanceof Deno.errors.NotFound)return null;throw e;}
}
function outputFile(folder,name){return Deno.readFileSync(folder+'/'+name+'.bin');}
function fileStream(folder,name,done){
 let offset=0;
 return new ReadableStream({async pull(controller){
  for(;;){
   const bytes=outputFile(folder,name);
   if(bytes.length>offset){controller.enqueue(bytes.slice(offset));offset=bytes.length;return;}
   if(done.value){controller.close();return;}
   await sleep(10);
  }
 }});
}
class FileChild {
 constructor(native,folder,request){
  this.native=native;this.folder=folder;this.pid=native.pid;const done={value:false};
  this.status=native.status.then(status=>{
   done.value=true;const error=errorFrom(folder);if(error)throw error;
   if(!status.success)throw Error('QUARTO_PIPE_BRIDGE_EXIT:'+status.code);
   return JSON.parse(Deno.readTextFileSync(folder+'/status.json'));
  },error=>{done.value=true;throw error;});
  // status错误保留给调用者；流读者不造成未处理Promise拒绝。
  this.status.catch(()=>{});
  if(request.stdin==='piped')this.stdin=new WritableStream({
   write(chunk){Deno.writeFileSync(folder+'/stdin.bin',chunk,{append:true});},
   close(){Deno.writeTextFileSync(folder+'/stdin.eof','');},
   abort(){native.kill('SIGTERM');}
  });
  if(request.stdout==='piped')this.stdout=fileStream(folder,'stdout',done);
  if(request.stderr==='piped')this.stderr=fileStream(folder,'stderr',done);
 }
 kill(signal='SIGTERM'){return this.native.kill(signal);}
 ref(){return this.native.ref();}
 unref(){return this.native.unref();}
 async output(){
  if(this.stdout?.locked||this.stderr?.locked)throw TypeError('stdout/stderr already locked');
  const status=await this.status;
  return {...status,stdout:this.stdout?outputFile(this.folder,'stdout'):new Uint8Array(),
    stderr:this.stderr?outputFile(this.folder,'stderr'):new Uint8Array()};
 }
}
Deno.Command=class QuartoFileCommand {
 constructor(command,options={}){this.command=command;this.options=options;this.native=new NativeCommand(command,options);}
 needsBridge(){return ['stdin','stdout','stderr'].some(k=>this.options[k]==='piped');}
 spawn(){
  if(!this.needsBridge())return this.native.spawn();
  const {folder,request}=prepare(this.command,this.options);
  const native=new NativeCommand(python,{args:['-S','-B',bridge,folder],stdin:'inherit',stdout:'inherit',stderr:'inherit'}).spawn();
  return new FileChild(native,folder,request);
 }
 async output(){
  if(this.options.stdin==='piped')throw TypeError('Piped stdin requires spawn()');
  return new QuartoFileCommand(this.command,{stdin:'null',stdout:'piped',stderr:'piped',...this.options}).spawn().output();
 }
 outputSync(){
  if(this.options.stdin==='piped')throw TypeError('Piped stdin requires spawn()');
  const {folder,request}=prepare(this.command,{stdin:'null',stdout:'piped',stderr:'piped',...this.options});
  const result=new NativeCommand(python,{args:['-S','-B',bridge,folder],stdin:'inherit',stdout:'inherit',stderr:'inherit'}).outputSync();
  const error=errorFrom(folder);if(error)throw error;if(!result.success)throw Error('QUARTO_PIPE_BRIDGE_EXIT:'+result.code);
  return {...JSON.parse(Deno.readTextFileSync(folder+'/status.json')),
    stdout:request.stdout==='piped'?outputFile(folder,'stdout'):new Uint8Array(),
    stderr:request.stderr==='piped'?outputFile(folder,'stderr'):new Uint8Array()};
 }
};
