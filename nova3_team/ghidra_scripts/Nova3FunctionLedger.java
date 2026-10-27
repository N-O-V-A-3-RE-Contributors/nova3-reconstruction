// NOVA3: export only program identity-neutral function BOUNDARIES.
// Run as a Ghidra postScript; NEVER export disassembly, bytes, names or decompiled code.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.MemoryBlock;
import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.nio.file.Path;

public class Nova3FunctionLedger extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("Usage: Nova3FunctionLedger.java /absolute/path/result.json");
        }
        Path output = Paths.get(args[0]).toAbsolutePath().normalize();
        if (output.getParent() == null || !Files.isDirectory(output.getParent())) {
            throw new IllegalArgumentException("Output directory does not exist");
        }
        long base = currentProgram.getImageBase().getOffset();
        int count = 0;
        try (BufferedWriter out = Files.newBufferedWriter(output, StandardCharsets.UTF_8)) {
            out.write("{\"schema_version\":1,\"normalization\":\"image-base-relative\",\"functions\":[");
            FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
            while (it.hasNext()) {
                monitor.checkCancelled();
                Function fn = it.next();
                if (fn.isExternal()) continue;
                Address entry = fn.getEntryPoint();
                if (entry == null || !entry.isMemoryAddress()) continue;
                MemoryBlock block = currentProgram.getMemory().getBlock(entry);
                if (block == null || !block.isExecute()) continue;
                long address = entry.getOffset() - base;
                long size = fn.getBody().getNumAddresses();
                if (address < 0 || size <= 0 || size > 1048576L) continue;
                if (count++ > 0) out.write(",");
                out.write("{\"address\":\"0x" + Long.toHexString(address) + "\",\"size\":" + size + "}");
            }
            out.write("]}\n");
        }
        println("NOVA3_METADATA_EXPORTED " + count + " " + output.toString());
    }
}
